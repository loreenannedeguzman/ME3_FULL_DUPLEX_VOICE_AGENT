"""Fresh-allowance console for a read-only final benchmark; no mutation/reset action."""
import argparse,hashlib,json,platform,signal,subprocess,sys,time
from pathlib import Path
from datetime import datetime,timezone
from benchmark_final.console import Controller as PreviousController,verify_environment
from benchmark_m6.console import environment,task_state
from benchmark_m6.storage import read,write_new,rows,queue
from benchmark_agent.protocol import ROOT,PHASE,FROZEN,SESSIONS,TRIALS,LIMITS,METHODS,VERSION,run_path
def verify_sources():
    source=read(FROZEN/'source.json')
    if any(hashlib.sha256((ROOT/n).read_bytes()).hexdigest()!=h for n,h in source['source_hashes'].items()):raise ValueError('frozen_runtime_changed')
    return source
def harness_source():
    paths=list((ROOT/'benchmark_agent').glob('*.py'))+list((ROOT/'benchmark_final').glob('*.py'))
    paths+=[ROOT/'tests/test_agent_benchmark.py',ROOT/'tests/test_final_benchmark.py']
    hashes={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}
    return {'source_hashes':hashes,'revision':hashlib.sha256(json.dumps(hashes,sort_keys=True,separators=(',',':')).encode()).hexdigest()}
def authorization(name):
    run_path(name);value=read(PHASE/('authorization-'+name+'.json'))
    if value.get('approved') is not True or value.get('run_id')!=name or value.get('limits')!=LIMITS or value.get('existing_google_allowance_confirmed') is not True:raise ValueError('fresh_exact_allowance_required')
    return value
def init(name):
    source=verify_sources();auth=authorization(name);env=environment();provenance=read(FROZEN/'pi-provenance.json');verify_environment(env,provenance['environment']);_,items,state=task_state()
    if state['state_digests']!=provenance['task_state']['state_digests']:raise ValueError('frozen_six_row_state_changed')
    folder=run_path(name);folder.mkdir(parents=True,exist_ok=False)
    write_new(folder/'manifest.json',{'run_id':name,'created_utc':datetime.now(timezone.utc).isoformat(),'protocol_version':VERSION,'protocol':SESSIONS,'methods':METHODS,'request_limits':LIMITS,'source':source,'harness':harness_source(),'authorization':auth,'environment':env,'initial_task_state':state,'recordings':'No raw mic audio or ASR transcripts','task_policy':'Read-only existing six rows; no write or reset; task is not matched to historical write'})
    write_new(folder/'budget.json',{k:0 for k in LIMITS});return folder
class Controller(PreviousController):
    def __init__(self,name):self.name=name;self.folder=run_path(name);self.children={};self.logfiles={}
    def reap(self):
        for session,child in self.children.items():
            path=self.folder/session/'process-exit.json'
            if child.poll() is not None and not path.exists():write_new(path,{'native_exit':child.returncode,'observed_utc':datetime.now(timezone.utc).isoformat(),'pid':child.pid,'command':[sys.executable,'-B','-m','benchmark_agent.runner',self.name,session]});self.logfiles[session].close()
    def action(self,command):
        self.reap();action=command.get('action');session=command.get('session')
        if action=='prepare_task':raise ValueError('no_deletions_or_writes_supported')
        if action=='start':
            verify_sources();authorization(self.name)
            if harness_source()!=read(self.folder/'manifest.json')['harness']:raise ValueError('harness_changed')
            if session not in SESSIONS or session in self.children or (self.folder/session).exists() or any(x.poll() is None for x in self.children.values()):raise ValueError('session_not_available')
            if any(x.poll() not in (0,None,130) for x in self.children.values()):raise ValueError('prior_failure_no_automatic_relaunch')
            log=(self.folder/(session+'-terminal.log')).open('x',encoding='utf-8');self.logfiles[session]=log
            child=subprocess.Popen([sys.executable,'-B','-m','benchmark_agent.runner',self.name,session],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL);self.children[session]=child;return {'started':session,'pid':child.pid,'max_seconds':600,'semantic_turns':8}
        if action=='verify_task':
            data=rows(self.folder/'F/events.jsonl');read_event=next((r for r in reversed(data) if r['event']=='task_mcp_request_end' and r.get('tool')=='list_items' and r.get('trial_id')=='F02'),None)
            if read_event is None:raise ValueError('no_F02_actual_list_tool')
            _,items,state=task_state();before=read(self.folder/'manifest.json')['initial_task_state'];rulers=[x for x in items if x['item']=='rulers' and x['quantity']==6]
            result={'verified':state['state_digests']==before['state_digests'] and len(items)==6 and len(rulers)==1,'all_six_rows_unchanged':state['state_digests']==before['state_digests'],'observed_ns':time.monotonic_ns(),'verifier_boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),'method':'standalone read-only SQLite outside task MCP/store, actual list tool event required','row_count':len(items),'database_sha256':state['database_sha256'],'no_write':True}
            write_new(self.folder/'task-independent-verification.json',result);queue(self.folder/'F',{'action':'verification','trial':'F02','result':result})
            if not result['verified']:raise ValueError('readback_state_mismatch')
            return result
        if action=='summary':
            from benchmark_agent.analyze import analyze
            result=analyze(self.folder);path=self.folder/('summary-'+str(len(list(self.folder.glob('summary-*.json')))+1).zfill(3)+'.json');write_new(path,result);return {'summary':path.relative_to(ROOT).as_posix(),'accounting':result['accounting'],'latency':result['normal_latency']}
        return super().action(command)
def main():
    p=argparse.ArgumentParser();p.add_argument('--run');p.add_argument('--new',action='store_true');p.add_argument('--check',action='store_true');a=p.parse_args()
    if a.check:print(json.dumps({'source':verify_sources()['revision'],'harness':harness_source()['revision'],'planned':len(TRIALS),'limits_require_fresh_allowance':LIMITS,'audio_opened':False,'provider_calls':0,'task':'read-only six rows'}));return
    if not a.run:p.error('--run required')
    if platform.system()!='Linux':raise ValueError('Pi_required')
    if a.new:init(a.run)
    else:authorization(a.run)
    c=Controller(a.run);print(json.dumps({'ready':True,'run_id':a.run}),flush=True)
    try:
        for line in sys.stdin:
            try:
                command=json.loads(line);print(json.dumps({'ok':True,'result':c.action(command)}),flush=True)
                if command.get('action')=='finish':break
            except Exception as exc:print(json.dumps({'ok':False,'error_type':type(exc).__name__,'reason':str(exc) if type(exc) is ValueError else 'inspect_saved_metadata'}),flush=True)
    finally:
        for child in c.children.values():
            if child.poll() is None:child.send_signal(signal.SIGINT);child.wait(timeout=20)
        c.reap()
if __name__=='__main__':main()
