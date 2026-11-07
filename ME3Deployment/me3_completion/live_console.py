"""Restricted functional-test console; no shell/ports and no automatic relaunch."""
import hashlib,json,platform,signal,sqlite3,subprocess,sys,time
from pathlib import Path
from contextlib import closing
from datetime import datetime,timezone
from benchmark_m6.storage import ROOT,read,rows,write_new,queue
from benchmark_m6.console import task_state,environment
from me3_completion.live_runner import run_path,PHASE
from me3_completion.scenarios import SESSIONS,LIMITS

def init(name):
    pointer=read(PHASE/'candidate-source-latest.json');source=read(PHASE/pointer['file']);assert all(hashlib.sha256((ROOT/n).read_bytes()).hexdigest()==h for n,h in source['source_hashes'].items())
    _,items,state=task_state()
    if any(x['item']=='markers' for x in items):raise ValueError('markers_already_present_choose_new_task_before_run')
    folder=run_path(name);folder.mkdir(exist_ok=False)
    write_new(folder/'manifest.json',{'created_utc':datetime.now(timezone.utc).isoformat(),'run_id':name,'source':source,'scenarios':SESSIONS,'limits':LIMITS,'environment':environment(),'initial_task_state':state,'target_task':{'item':'markers','quantity':4},'mode':'focused core functional validation; no comprehensive latency benchmark','controlled_timeout':'one actual mic ASR request under local100ms deadline; not a natural provider failure','retention':'metadata only; no raw mic audio or ASR transcripts'})
    if not (PHASE/'functional-budget.json').exists():write_new(PHASE/'functional-budget.json',{k:(2 if k=='tts' else 0) for k in LIMITS})
class Console:
    def __init__(self,name):self.name=name;self.folder=run_path(name);self.children={};self.logs={}
    def reap(self):
        for session,child in self.children.items():
            path=self.folder/session/'process-exit.json'
            if child.poll() is not None and not path.exists():write_new(path,{'native_exit':child.poll(),'pid':child.pid,'observed_utc':datetime.now(timezone.utc).isoformat(),'command':[sys.executable,'-m','me3_completion.live_runner',self.name,session]});self.logs[session].close()
    def action(self,cmd):
        self.reap();action=cmd.get('action');session=cmd.get('session')
        if session is not None and session not in SESSIONS:raise ValueError('invalid_session')
        if action=='start':
            if session in self.children or (self.folder/session).exists() or any(p.poll() is None for p in self.children.values()):raise ValueError('session_unavailable')
            log=(self.folder/(session+'-terminal.log')).open('x',encoding='utf-8');self.logs[session]=log
            child=subprocess.Popen([sys.executable,'-m','me3_completion.live_runner',self.name,session],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL);self.children[session]=child
            return {'session':session,'pid':child.pid,'max_seconds':600,'max_semantic_turns':8,'max_asr_probe_attempts':32,'max_segments_per_turn':4}
        if action=='status':
            data=rows(self.folder/session/'events.jsonl');selected={'core_episode_start','core_turn_started','core_turn_settled','completion_ownership','completion_turn_held','completion_agent_backchannel','completion_user_backchannel','completion_overlap_classified','completion_background_error','core_empty_speech_clarification','m7_segment_decision','m7_clean_timeout_recoverable','core_controlled_deadline','core_turn_deadline','mcp_request_error','response_complete','task_independently_verified','session_stop','turn_budget_reached'}
            return {'native_exit':self.children[session].poll(),'budget':read(PHASE/'functional-budget.json'),'latest':[x for x in data if x['event'] in selected][-8:]}
        if action in {'cue','arm_timeout'}:
            if session not in self.children or self.children[session].poll() is not None:raise ValueError('session_not_running')
            return queue(self.folder/session,{'action':action,'episode':cmd.get('episode')})
        if action=='report':
            episode=cmd.get('episode');data=rows(self.folder/session/'events.jsonl')
            if episode not in SESSIONS[session] or not any(x['event']=='core_episode_start' and x.get('episode_id')==episode for x in data):raise ValueError('episode_not_started')
            if cmd.get('outcome') not in {'pass','fail','incomplete'}:raise ValueError('invalid_outcome')
            fields={k:v for k,v in cmd.items() if k in {'outcome','audible','correct','overlap','stopped','no_replay','note'}}
            for key in ('audible','correct','overlap','stopped','no_replay'):
                if key in fields and type(fields[key]) is not bool:raise ValueError('invalid_operator_observation')
            if fields.get('note') not in {None,'no_reply','wrong_answer','unclear','old_returned','expected_controlled_timeout','other_failure'}:raise ValueError('invalid_note')
            record={'episode_id':episode,'observed_utc':datetime.now(timezone.utc).isoformat(),'source':'direct_human_observation_in_Codex','native_exit_when_reported':self.children[session].poll(),**fields}
            with (self.folder/session/'operator.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(record)+'\n')
            if self.children[session].poll() is None:return queue(self.folder/session,{'action':'end_episode','episode':episode})
            return {'recorded_after_exit':True}
        if action=='verify_task':
            data=[row for name in SESSIONS for row in rows(self.folder/name/'events.jsonl')];event=next((x for x in reversed(data) if x['event']=='task_independently_verified' and x.get('episode_id')=='E2'),None)
            if event is None:raise ValueError('task_not_verified_by_agent')
            path,items,state=task_state();row=next((r for r in items if r['operation_id']==event['operation_id']),None);baseline=read(self.folder/'manifest.json')['initial_task_state']['state_digests'];new=state['state_digests']
            ok=bool(row and row['item']=='markers' and row['quantity']==4 and new.get(row['operation_id'])==event['state_digest'] and len(new)==len(baseline)+1 and all(new.get(k)==v for k,v in baseline.items()))
            result={'observed_ns':time.monotonic_ns(),'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),'verified':ok,'operation_id':event['operation_id'],'state_digest':state['state_digests'].get(event['operation_id']),'database_sha256':state['database_sha256'],'original_rows_unchanged':all(new.get(k)==v for k,v in baseline.items()),'row_count':len(new),'method':'standalone read-only SQLite query outside Task MCP/store code'}
            write_new(self.folder/'task-independent-verification.json',result)
            if not ok:raise ValueError('independent_task_mismatch')
            return result
        if action=='stop':
            child=self.children[session]
            if child.poll() is None:child.send_signal(signal.SIGINT);child.wait(timeout=20)
            self.reap();return read(self.folder/session/'process-exit.json')
        if action=='finish':
            if any(p.poll() is None for p in self.children.values()):raise ValueError('sessions_still_running')
            _,_,state=task_state();write_new(self.folder/(('final-task-state-'+next(reversed(self.children))+'.json') if (self.folder/'final-task-state.json').exists() else 'final-task-state.json'),state);return {'finished':True,'budget':read(PHASE/'functional-budget.json'),'task_state':state}
        raise ValueError('invalid_action')
def console(name,new=False):
    if platform.system()!='Linux':raise ValueError('live_console_requires_Pi')
    if new:init(name)
    c=Console(name);print(json.dumps({'ready':True,'run_id':name}),flush=True)
    try:
        for line in sys.stdin:
            try:
                cmd=json.loads(line);print(json.dumps({'ok':True,'result':c.action(cmd)}),flush=True)
                if cmd['action']=='finish':break
            except Exception as exc:print(json.dumps({'ok':False,'error_type':type(exc).__name__,'reason':str(exc) if type(exc) is ValueError else 'inspect_saved_metadata'}),flush=True)
    finally:
        for child in c.children.values():
            if child.poll() is None:child.send_signal(signal.SIGINT);child.wait(timeout=20)
        c.reap()
