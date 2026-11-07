"""Restricted benchmark console; explicit fresh allowance required, no data deletion."""
import argparse,hashlib,json,platform,signal,subprocess,sys,time
from pathlib import Path
from datetime import datetime,timezone
from benchmark_m6.console import Controller as BaseController,task_state,environment
from benchmark_m6.storage import read,rows,write_new,queue
from benchmark_final.protocol import ROOT,PHASE,FROZEN,SESSIONS,TRIALS,LIMITS,METHODS,VERSION,run_path

def verify_sources():
    source=read(FROZEN/'source.json')
    if any(hashlib.sha256((ROOT/n).read_bytes()).hexdigest()!=h for n,h in source['source_hashes'].items()):raise ValueError('frozen_runtime_changed')
    return source

def harness_source():
    paths=list((ROOT/'benchmark_final').glob('*.py'))
    if (ROOT/'tests/test_final_benchmark.py').exists():paths.append(ROOT/'tests/test_final_benchmark.py')
    h={x.relative_to(ROOT).as_posix():hashlib.sha256(x.read_bytes()).hexdigest() for x in sorted(paths)}
    return {'source_hashes':h,'revision':hashlib.sha256(json.dumps(h,sort_keys=True,separators=(',',':')).encode()).hexdigest()}
def authorization(name):
    run_path(name) # validate before any authorization-file read
    data=read(PHASE/('authorization-'+name+'.json'))
    if data.get('approved') is not True or data.get('run_id')!=name or data.get('limits')!=LIMITS or data.get('existing_google_allowance_confirmed') is not True:raise ValueError('fresh_explicit_allowance_required')
    return data

def verify_environment(current,expected):
    fields=['asr_model','text_model','model_sha256','model_config_sha256','input_index','output_index','tts_provider','worker_mode','voice','vad','playback']
    if any(current.get(k)!=expected.get(k) for k in fields):raise ValueError('frozen_model_or_audio_configuration_changed')
def init(name):
    source=verify_sources();auth=authorization(name);env=environment();verify_environment(env,read(FROZEN/'pi-provenance.json')['environment']);_,items,state=task_state()
    if any(x['item']=='rulers' for x in items):raise ValueError('target_item_already_exists_no_delete')
    folder=run_path(name);folder.mkdir(exist_ok=False)
    write_new(folder/'manifest.json',{'run_id':name,'created_utc':datetime.now(timezone.utc).isoformat(),'protocol_version':VERSION,'protocol':SESSIONS,'methods':METHODS,'request_limits':LIMITS,'source':source,'harness':harness_source(),'authorization':auth,'environment':env,'initial_task_state':state,'target':{'item':'rulers','quantity':6},'recordings':'no raw mic audio or ASR transcripts','comparison':'20normal scripts match M6; task is a different nondestructive case, not a matched task speedup'})
    write_new(folder/'budget.json',{k:0 for k in LIMITS});return folder
class Controller(BaseController):
    def __init__(self,name):self.name=name;self.folder=run_path(name);self.children={};self.logfiles={}
    def reap(self):
        for session,child in self.children.items():
            target=self.folder/session/'process-exit.json'
            if child.poll() is not None and not target.exists():write_new(target,{'native_exit':child.returncode,'observed_utc':datetime.now(timezone.utc).isoformat(),'pid':child.pid,'command':[sys.executable,'-m','benchmark_final.runner',self.name,session]});self.logfiles[session].close()
    def action(self,command):
        self.reap();action=command.get('action');session=command.get('session')
        if action=='prepare_task':raise ValueError('no_deletions_supported')
        if action=='start':
            verify_sources();expected=read(self.folder/'manifest.json')['harness']
            if harness_source()!=expected:raise ValueError('benchmark_harness_changed')
            if session not in SESSIONS or session in self.children or (self.folder/session).exists() or any(x.poll() is None for x in self.children.values()):raise ValueError('session_not_available')
            if any(x.poll() not in (0,None,130) for x in self.children.values()):raise ValueError('prior_failure_no_automatic_relaunch')
            log=(self.folder/(session+'-terminal.log')).open('x',encoding='utf-8');self.logfiles[session]=log
            child=subprocess.Popen([sys.executable,'-m','benchmark_final.runner',self.name,session],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL);self.children[session]=child
            return {'started':session,'pid':child.pid,'max_seconds':600,'max_semantic_turns':8}
        if action=='verify_task':
            data=rows(self.folder/'F/events.jsonl');event=next((x for x in reversed(data) if x['event']=='task_independently_verified' and x.get('trial_id')=='F02'),None)
            if event is None:raise ValueError('no_verified_task_event')
            _,items,state=task_state();row=next((x for x in items if x['operation_id']==event['operation_id']),None);old=read(self.folder/'manifest.json')['initial_task_state']['state_digests'];now=state['state_digests']
            result={'observed_ns':time.monotonic_ns(),'verifier_boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),'verified':bool(row and row['item']=='rulers' and row['quantity']==6 and now[row['operation_id']]==event['state_digest'] and len(now)==len(old)+1 and all(now.get(k)==v for k,v in old.items())),'original_rows_unchanged':all(now.get(k)==v for k,v in old.items()),'operation_id':event['operation_id'],'state_digest':now.get(event['operation_id']),'row_count':len(now),'database_sha256':state['database_sha256'],'method':'standalone read-only SQLite outside Task MCP/store code'}
            write_new(self.folder/'task-independent-verification.json',result);queue(self.folder/'F',{'action':'verification','trial':'F02','result':result})
            if not result['verified']:raise ValueError('task_mismatch')
            return result
        if action=='summary':
            from benchmark_final.analyze import analyze
            result=analyze(self.folder);path=self.folder/('summary-'+str(len(list(self.folder.glob('summary-*.json')))+1).zfill(3)+'.json');write_new(path,result)
            return {'summary':path.relative_to(ROOT).as_posix(),'accounting':result['accounting'],'latency':result['normal_latency']}
        if action=='report' and session in self.children and self.children[session].poll() is not None:
            trial=command.get('trial');data=rows(self.folder/session/'events.jsonl')
            if trial not in TRIALS or not any(x['event']=='m6_trial_start' and x.get('trial_id')==trial for x in data):raise ValueError('trial_not_started')
            fields={k:command[k] for k in ('outcome','audible','response_correct','overlap','old_stopped','no_replay','note') if k in command}
            if fields.get('outcome') not in {'pass','fail','incomplete','timeout'}:raise ValueError('invalid_outcome')
            if any(type(v) is not bool for k,v in fields.items() if k not in {'outcome','note'}):raise ValueError('invalid_observation')
            if fields.get('note') not in {None,'none','wrong_answer','no_reply','unclear_audio','old_returned','split_not_detected','budget','operator_unavailable'}:raise ValueError('invalid_note')
            late=self.folder/session/'operator-late.jsonl'
            if any(x.get('trial_id')==trial for x in rows(late)) or any(x['event']=='m6_trial_report' and x.get('trial_id')==trial for x in data):raise ValueError('duplicate_report')
            with late.open('a',encoding='utf-8') as f:f.write(json.dumps({'event':'m6_trial_report','trial_id':trial,'observed_ns':time.monotonic_ns(),'source':'direct operator report after process exit',**fields})+'\n')
            return {'recorded_after_exit':True}
        return super().action(command)
def main():
    p=argparse.ArgumentParser();p.add_argument('--run');p.add_argument('--new',action='store_true');p.add_argument('--check',action='store_true');a=p.parse_args()
    if a.check:
        source=verify_sources();print(json.dumps({'runtime_revision':source['revision'],'harness':harness_source(),'planned_trials':len(TRIALS),'normal_trials':sum(x['kind']=='normal' for x in TRIALS.values()),'limits_require_new_authorization':LIMITS,'provider_calls':0,'audio_opened':False,'task':'six rulers, direct confirmation, original rows preserved'}));return
    if not a.run:p.error('--run is required unless --check is used')
    if platform.system()!='Linux':raise ValueError('Pi_required_for_live_benchmark')
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
