"""Restricted project-local controller over SSH stdin; no shell execution or listening port."""
import collections,hashlib,json,os,platform,signal,sqlite3,subprocess,sys,time,shutil
from contextlib import closing
from datetime import datetime,timezone
from pathlib import Path
from dotenv import dotenv_values
from benchmark_m6.protocol import VERSION,SESSIONS,TRIALS,METHODS,LIMITS
from benchmark_m6.storage import ROOT,read,write_new,atomic,run_path,rows,queue
from benchmark_m6.analyze import analyze

def environment():
    cfg=dotenv_values(ROOT/'.env',encoding='utf-8-sig');model=ROOT/'models/piper/en_US-ljspeech-high.onnx'
    info={'platform':platform.uname()._asdict(),'python':platform.python_version(),'monotonic':{'implementation':time.get_clock_info('monotonic').implementation,'resolution_seconds':time.get_clock_info('monotonic').resolution},'credential_present':bool(cfg.get('GOOGLE_API_KEY')),'audio_transfer_authorized':cfg.get('ALLOW_PROVIDER_AUDIO')=='1','tts_provider':'piper','worker_mode':'resident','voice':'en_US-ljspeech-high','model_sha256':hashlib.sha256(model.read_bytes()).hexdigest() if model.exists() else None,'model_config_sha256':hashlib.sha256(Path(str(model)+'.json').read_bytes()).hexdigest() if Path(str(model)+'.json').exists() else None,'asr_model':cfg.get('ASR_MODEL','gemini-3.5-flash-lite'),'text_model':cfg.get('TEXT_MODEL','gemini-3.5-flash-lite'),'input_index':cfg.get('INPUT_DEVICE_INDEX'),'output_index':cfg.get('OUTPUT_DEVICE_INDEX'),'vad':{'confidence':.7,'start_seconds':.15,'stop_seconds':.7,'min_volume':.6,'preroll_ms':300},'playback':{'rate':48000,'channels':2,'queue_ms':120,'callback_ms':20},'free_disk_bytes':shutil.disk_usage(ROOT).free,'raw_audio_or_transcripts_retained':False,'acoustic_hardware':'earphones/USB microphone; no calibrated loopback or reference recorder'}
    if platform.system()=='Linux':
        info['os_release']=Path('/etc/os-release').read_text();info['pi_model']=Path('/proc/device-tree/model').read_text().rstrip('\x00');info['boot_id']=Path('/proc/sys/kernel/random/boot_id').read_text().strip();info['thermal_millidegrees']=int(Path('/sys/class/thermal/thermal_zone0/temp').read_text());info['throttled']=subprocess.run(['vcgencmd','get_throttled'],capture_output=True,text=True).stdout.strip()
    return info

def init(name):
    folder=run_path(name);folder.mkdir(exist_ok=False)
    manifest=read(ROOT/'evidence/m6/source-001.json');assert all(hashlib.sha256((ROOT/f).read_bytes()).hexdigest()==h for f,h in manifest['source_hashes'].items()),'source_mismatch'
    write_new(folder/'manifest.json',{'run_id':name,'created_utc':datetime.now(timezone.utc).isoformat(),'protocol_version':VERSION,'protocol':SESSIONS,'methods':METHODS,'request_limits':LIMITS,'source':manifest,'environment':environment(),'prior_failures':{'semantic':'evidence/m5/reuse/operator-live-001.json','segmentation':'evidence/m5/reuse/operator-live-002.json','scope':'Prior failures preserved; separate from new prospective trial counts'}})
    write_new(folder/'budget.json',{k:0 for k in LIMITS})
    return folder

def task_state():
    path=(ROOT/'data/shopping.sqlite3').resolve();assert path.is_relative_to((ROOT/'data').resolve())
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=1)) as db:
        db.row_factory=sqlite3.Row;data=[dict(x) for x in db.execute('SELECT operation_id,item,quantity,created_utc FROM items')]
    digests={x['operation_id']:hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':')).encode()).hexdigest() for x in data}
    return path,data,{'row_count':len(data),'state_digests':digests,'database_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'database_mode':oct(path.stat().st_mode&0o777)}

def prepare_task(folder,authorized):
    if authorized is not True:raise ValueError('explicit_row_removal_authorization_required')
    target=folder/'task-preparation.json'
    if target.exists():raise ValueError('task_preparation_already_recorded')
    path,data,before=task_state();operation='e9b5b19230d4448095d199e5c32d5d18'
    row=next((x for x in data if x['operation_id']==operation),None)
    if not row or row['item']!='pencils' or row['quantity']!=5 or before['state_digests'][operation]!='64a92b51174c95f08241443a87ca73bb020a5f82f1aa8f3149568863c9e9b207':raise ValueError('expected_approved_pencil_row_not_found')
    with closing(sqlite3.connect(path,timeout=1)) as db:
        db.execute('BEGIN IMMEDIATE');cur=db.execute('DELETE FROM items WHERE operation_id=? AND item=? AND quantity=?',(operation,'pencils',5));assert cur.rowcount==1;db.commit()
    _,remaining,after=task_state();expected={k:v for k,v in before['state_digests'].items() if k!=operation};assert after['state_digests']==expected
    result={'observed_ns':time.monotonic_ns(),'user_authorized':True,'deleted_only_operation':operation,'original_evidence_preserved':True,'before':before,'after':after,'all_other_rows_identical':True};write_new(target,result);return result

def verify_task(folder,session):
    data=rows(folder/session/'events.jsonl');event=next((x for x in reversed(data) if x['event']=='task_independently_verified' and x.get('trial_id')=='F02'),None)
    if event is None:raise ValueError('no_verified_F02_operation')
    _,items,state=task_state();row=next((x for x in items if x['operation_id']==event['operation_id']),None)
    result={'observed_ns':time.monotonic_ns(),'clock':'Pi CLOCK_MONOTONIC via time.monotonic_ns; same host/boot','verifier_boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),'operation_id':event['operation_id'],'verified':bool(row and row['item']=='pencils' and row['quantity']==5 and state['state_digests'][event['operation_id']]==event['state_digest']),'method':'standalone read-only SQLite query, outside task MCP/store code','state_digest':state['state_digests'].get(event['operation_id']),'database_sha256':state['database_sha256'],'row_count':len(items)}
    target=folder/'task-independent-verification.json';write_new(target,result)
    queue(folder/session,{'action':'verification','trial':'F02','result':result})
    if not result['verified']:raise ValueError('independent_task_mismatch')
    return result

class Controller:
    def __init__(self,name):self.name=name;self.folder=run_path(name);self.children={};self.logfiles={}
    def reap(self):
        for session,child in self.children.items():
            code=child.poll();target=self.folder/session/'process-exit.json'
            if code is not None and not target.exists():
                write_new(target,{'native_exit':code,'observed_utc':datetime.now(timezone.utc).isoformat(),'pid':child.pid,'command':[sys.executable,'-m','benchmark_m6.runner',self.name,session]});self.logfiles[session].close()
    def action(self,command):
        self.reap();action=command.get('action');session=command.get('session')
        if session is not None and session not in SESSIONS:raise ValueError('invalid_session')
        if action=='start':
            if session not in SESSIONS or session in self.children or any(c.poll() is None for c in self.children.values()):raise ValueError('session_not_available')
            folder=self.folder/session
            if folder.exists():raise ValueError('session_evidence_exists')
            # Parent creates log outside the child event directory (EventLog owns directory).
            log=(self.folder/(session+'-terminal.log')).open('x',encoding='utf-8');self.logfiles[session]=log
            child=subprocess.Popen([sys.executable,'-m','benchmark_m6.runner',self.name,session],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL);self.children[session]=child
            return {'started':session,'pid':child.pid,'max_seconds':600,'max_human_turns':8}
        if action=='status':
            data=rows(self.folder/session/'events.jsonl');counter=collections.Counter(x['event'] for x in data)
            selected={'m6_trial_start','m6_trial_report','m6_speech_onset_proxy','m6_endpoint','mcp_request_start','mcp_request_end','mcp_request_cancelled','mcp_request_error','tts_backend_metrics','response_complete','turn_error','turn_budget_reached','task_independently_verified','m6_task_external_verification','session_stop'}
            return {'session':session,'pid':self.children[session].pid if session in self.children else None,'native_exit':self.children[session].poll() if session in self.children else None,'counts':dict(counter),'latest':[x for x in data if x['event'] in selected][-10:],'budget':read(self.folder/'budget.json')}
        if action in {'cue','report'}:
            if session not in self.children or self.children[session].poll() is not None:raise ValueError('session_not_running')
            return queue(self.folder/session,{k:v for k,v in command.items() if k!='session'})
        if action=='stop':
            child=self.children[session]
            if child.poll() is None:
                child.send_signal(signal.SIGINT)
                try:child.wait(timeout=20)
                except subprocess.TimeoutExpired:raise ValueError('session_stop_timeout_no_hidden_kill')
            self.reap();return read(self.folder/session/'process-exit.json')
        if action=='prepare_task':return prepare_task(self.folder,command.get('authorized'))
        if action=='verify_task':return verify_task(self.folder,session)
        if action=='summary':
            data=analyze(self.folder);number=len(list(self.folder.glob('summary-*.json')))+1;path=self.folder/f'summary-{number:03}.json';write_new(path,data)
            return {'summary_file':str(path.relative_to(ROOT)),'accounting':data['trial_accounting'],'normal_latency':data['steady_normal_all_available_including_semantic_failures'],'split':data['split_recovery'],'interruptions':data['interruption_summary']}
        if action=='finish':
            if any(c.poll() is None for c in self.children.values()):raise ValueError('sessions_still_running')
            _,_,state=task_state();write_new(self.folder/'task-final-state.json',state)
            return {'finished':True,'budget':read(self.folder/'budget.json'),'task_state':state}
        raise ValueError('unknown_controller_action')

def console(name,new=False):
    if platform.system()!='Linux':raise ValueError('live_console_requires_Pi_Linux; Windows tests are offline only')
    if new:init(name)
    controller=Controller(name);print(json.dumps({'ready':True,'run_id':name,'allowed_actions':['start','cue','report','status','stop','prepare_task','verify_task','summary','finish']}),flush=True)
    for line in sys.stdin:
        try:
            command=json.loads(line);result=controller.action(command);print(json.dumps({'ok':True,'result':result}),flush=True)
            if command['action']=='finish':break
        except Exception as exc:print(json.dumps({'ok':False,'error_type':type(exc).__name__,'reason':str(exc) if type(exc) is ValueError else 'inspect_native_evidence'}),flush=True)
    for session,child in controller.children.items():
        if child.poll() is None:child.send_signal(signal.SIGINT);child.wait(timeout=20)
    controller.reap()
