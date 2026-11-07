"""Restricted M7 controller; original protocol, explicit current-row task guard and preserved M6."""
import collections,hashlib,json,os,platform,re,signal,sqlite3,subprocess,sys,time
from contextlib import closing
from datetime import datetime,timezone
from pathlib import Path
import benchmark_m6.console as old
from benchmark_m6.protocol import SESSIONS,METHODS,LIMITS
from benchmark_m6.storage import ROOT,read,write_new,rows
from me3_m7.benchmark_runner import run_path
from me3_m7.analyze import analyze
old.run_path=run_path;old.analyze=analyze

def init(name,source_name="source-live-001.json",inherit_run=None):
    folder=run_path(name);folder.mkdir(exist_ok=False)
    if not re.fullmatch(r'source-[a-z0-9-]+\.json',source_name):raise ValueError('invalid_source_manifest')
    source=read(ROOT/'evidence/m7'/source_name);assert all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h for p,h in source['source_hashes'].items())
    _,items,state=old.task_state();pencils=[x for x in items if x['item']=='pencils' and x['quantity']==5]
    if len(pencils)!=1:raise ValueError('expected_one_current_five_pencil_row')
    op=pencils[0]['operation_id'];target={'operation_id':op,'state_digest':state['state_digests'][op]}
    write_new(folder/'manifest.json',{'run_id':name,'created_utc':datetime.now(timezone.utc).isoformat(),'protocol_version':'m7-v1.0; same31M6scripts','protocol':SESSIONS,'methods':METHODS,'request_limits':LIMITS,'source':source,'environment':old.environment(),'asr_config':read(ROOT/'config/m7_asr.json'),'task_target':target,'baseline':'M6run-20261020-001 immutable','changed_policy':'max2cleanedASRtimeouts/session may recover on next explicit utterance, no automatic retry; M6 stopped entire batch','capture_method':'PCM frame arrival at VAD/preroll range, separately logged callback clocks; not physical onset'})
    budget=read(run_path(inherit_run)/'budget.json') if inherit_run else {k:0 for k in LIMITS}
    if set(budget)!=set(LIMITS) or any(type(v) is not int or not 0<=v<=LIMITS[k] for k,v in budget.items()):raise ValueError('invalid_inherited_budget')
    if inherit_run:write_new(folder/'inherited-budget.json',{'prior_run':inherit_run,'prior_usage':budget,'scope':'explicit supplementary subset; not a comparable full benchmark; no fresh request allowance'})
    write_new(folder/'budget.json',budget)

def prepare(folder,authorized):
    if authorized is not True:raise ValueError('explicit_row_removal_authorization_required')
    target=folder/'task-preparation.json'
    if target.exists():raise ValueError('task_preparation_already_recorded')
    guard=read(folder/'manifest.json')['task_target'];path,items,before=old.task_state();row=next((x for x in items if x['operation_id']==guard['operation_id']),None)
    if not row or row['item']!='pencils' or row['quantity']!=5 or before['state_digests'].get(guard['operation_id'])!=guard['state_digest']:raise ValueError('approved_row_guard_mismatch')
    with closing(sqlite3.connect(path,timeout=1)) as db:
        db.execute('BEGIN IMMEDIATE');cursor=db.execute('DELETE FROM items WHERE operation_id=? AND item=? AND quantity=?',(guard['operation_id'],'pencils',5));assert cursor.rowcount==1;db.commit()
    _,_,after=old.task_state();assert after['state_digests']=={k:v for k,v in before['state_digests'].items() if k!=guard['operation_id']}
    result={'observed_ns':time.monotonic_ns(),'user_authorized':True,'deleted_only_operation':guard['operation_id'],'before':before,'after':after,'all_other_rows_identical':True};write_new(target,result);return result
class Controller(old.Controller):
    def reap(self):
        for session,child in self.children.items():
            target=self.folder/session/'process-exit.json';code=child.poll()
            if code is not None and not target.exists():write_new(target,{'native_exit':code,'observed_utc':datetime.now(timezone.utc).isoformat(),'pid':child.pid,'command':[sys.executable,'-m','me3_m7.benchmark_runner',self.name,session]});self.logfiles[session].close()
    def action(self,command):
        self.reap();action=command.get('action');session=command.get('session')
        if action=='start':
            if session not in SESSIONS or session in self.children or any(c.poll() is None for c in self.children.values()) or (self.folder/session).exists():raise ValueError('session_not_available')
            log=(self.folder/(session+'-terminal.log')).open('x',encoding='utf-8');self.logfiles[session]=log
            child=subprocess.Popen([sys.executable,'-m','me3_m7.benchmark_runner',self.name,session],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL);self.children[session]=child
            return {'started':session,'pid':child.pid,'max_seconds':600,'max_human_turns':8}
        if action=='prepare_task':return prepare(self.folder,command.get('authorized'))
        if action=='report' and session in self.children and self.children[session].poll() is not None:
            trial=command.get('trial');data=rows(self.folder/session/'events.jsonl')
            if trial not in {x.get('trial_id') for x in data if x['event']=='m6_trial_start'} or command.get('outcome') not in {'pass','fail','timeout','incomplete'}:raise ValueError('invalid_closed_trial_report')
            row={'event':'m6_trial_report','session_id':session,'trial_id':trial,'monotonic_ns':time.monotonic_ns(),'source':'operator_after_exit',**{k:v for k,v in command.items() if k in {'outcome','audible','response_correct','overlap','old_stopped','no_replay','note'}}}
            for k in ('audible','response_correct','overlap','old_stopped','no_replay'):
                if k in row and type(row[k]) is not bool:raise ValueError('invalid_operator_observation')
            if row.get('note') not in {None,'none','wrong_answer','no_reply','unclear_audio','old_returned','split_not_detected','budget','operator_unavailable'}:raise ValueError('invalid_note')
            with (self.folder/session/'operator-after-exit.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(row)+'\n')
            return {'status':'recorded_after_exit','native_exit':self.children[session].poll()}
        result=super().action(command)
        if action=='status':result['recent_m7']=[x for x in rows(self.folder/session/'events.jsonl') if x['event'] in {'m7_segment_decision','m7_release_barrier_complete','m7_clean_timeout_recoverable','m7_incomplete_prefix_wait'}][-6:]
        return result

def console(name,new=False,source_name="source-live-001.json",inherit_run=None):
    if platform.system()!='Linux':raise ValueError('live_console_requires_Pi')
    if new:init(name,source_name,inherit_run)
    c=Controller(name);print(json.dumps({'ready':True,'run_id':name}),flush=True)
    try:
        for line in sys.stdin:
            try:
                command=json.loads(line);result=c.action(command);print(json.dumps({'ok':True,'result':result}),flush=True)
                if command['action']=='finish':break
            except Exception as exc:print(json.dumps({'ok':False,'error_type':type(exc).__name__,'reason':str(exc) if type(exc) is ValueError else 'inspect_native_evidence'}),flush=True)
    finally:
        for child in c.children.values():
            if child.poll() is None:child.send_signal(signal.SIGINT);child.wait(timeout=20)
        c.reap()
