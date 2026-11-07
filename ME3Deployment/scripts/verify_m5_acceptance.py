"""Read-only source/live/quality artifact reconciliation; no providers/audio/database operations."""
from pathlib import Path
import hashlib,json,re,wave
r=Path(__file__).resolve().parents[1];e=r/'evidence/m5';c=e/'pi-copy-001';checks=[]
def load(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def check(name,value):checks.append({'check':name,'pass':bool(value)})
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
a=load(e/'voice-acceptance-002.json');m=load(e/'voice-source-002.json');start=load(e/'starting-revision-001.json');live=load(c/'live-analysis-001.json');target=load(c/'target-source-003.json');operator=load(e/'operator-live-001.json')
for f,h in m['source_hashes'].items():
    check('Current source '+f,sha(r/f)==h);check('Frozen source '+f,sha(e/'voice-source-002'/f)==h)
for f,h in start['source_hashes'].items():check('Prior M4 frozen '+f,sha(e/'starting-source'/f)==h)
for f,h in start['accepted_receipts'].items():check('Accepted receipt unchanged '+f,sha(r/f)==h)
for f in m['unchanged_from_M4']:check('M4 runtime/test/dependency preserved '+f,sha(r/f)==start['source_hashes'][f])
check('Only five existing TTS interfaces extended',set(m['changed_from_M4'])=={'speech_mcp/tts_config.py','speech_mcp/tts_adapters.py','speech_mcp/client.py','speech_mcp/errors.py','speech_mcp/server.py'})
live_manifest=load(e/'voice-source-001.json');launch=load(e/'pi-final-copy-001/target-source-004.json')
check('Actual live target revision',load(c/'target-source-002.json')['revision_sha256']==live_manifest['revision_sha256']==target['revision_sha256']==a['live']['source_revision_sha256'] and target['all_source_hashes_match'] and target['source_hashes']==live_manifest['source_hashes'])
check('Current launch target revision',launch['revision_sha256']==m['revision_sha256'] and launch['source_hashes']==m['source_hashes'] and launch['all_match'] and launch['runtime_unchanged_from_live'] and launch['wrapper_only_CRLF_to_LF'])
check('Only launcher LF differs from live',[f for f,h in m['source_hashes'].items() if live_manifest['source_hashes'][f]!=h]==['scripts/run_piper_voice.sh'])
check('Wrapper byte-equivalent after normalization',(e/'voice-source-001/scripts/run_piper_voice.sh').read_bytes().replace(bytes([13,10]),bytes([10]))==(r/'scripts/run_piper_voice.sh').read_bytes())
for f,h in live_manifest['source_hashes'].items():check('Actual live frozen source '+f,sha(e/'voice-source-001'/f)==h)
check('Wrapper fail and correction preserved',load(e/'pi-final-copy-001/wrapper-build-001.json')['exit_code']==2 and load(e/'pi-final-copy-001/wrapper-build-002.json')['exit_code']==0 and not launch['remaining_speech_task_pids'])
check('Task DB unchanged after wrapper check',launch['database_sha256']==json.loads((c/'task-persistence-001.log').read_text())['database_sha256'])
check('Main dependency environment preserved',not target['main_locked_dependency_differences'] and not target['piper_in_main_environment'])
check('Model verified',target['model_sha256']==load(c/'piper-model-001.json')['verified_remote_sha256']=='5d4f08ba6a2a48c44592eed3ce56bf85e9de3dd4e20df90541ae68a8310c029a')
for name,base in [('windows-regressions-006',e),('pi-regressions-002',c)]:
    text=(base/(name+'.log')).read_text(encoding='utf-8-sig');check('105native tests '+name,load(base/(name+'.json'))['exit_code']==0 and 'Ran 105 tests' in text and re.search(r'\bOK\b',text))
for prefix,base in [('windows',e),('pi',c)]:
    for suffix in ['m1-build-001','m2-build-001','m3-build-001','m4-build-001','main-dependencies-001']:check('Native check '+prefix+'-'+suffix,load(base/(prefix+'-'+suffix+'.json'))['exit_code']==0)
for name in ['piper-mcp-cancel-002','voice-comparison-001','listening-short-002','listening-long-001','listening-question-001','listening-numbers-001','listening-followup-001','voice-live-001','task-independent-readback-001','task-persistence-001']:check('Pi native '+name,load(c/(name+'.json'))['exit_code']==0)
check('Failed real cancel retained',load(c/'piper-mcp-cancel-001.json')['exit_code']==1)
check('Failed Windows handshake iteration retained',load(e/'windows-regressions-005.json')['exit_code']==1 and 'FAILED (errors=2)' in (e/'windows-regressions-005.log').read_text())
measure=load(c/'comparison-001/measurements.json')
for x in measure['records']:
    p=c/'comparison-001'/x['file'];check('Generated WAV hash '+x['file'],sha(p)==x['sha256'])
    with wave.open(str(p),'rb') as wav:check('Generated WAV format '+x['file'],wav.getframerate()==24000 and wav.getnchannels()==1 and wav.getsampwidth()==2 and 0<wav.getnframes()<=24000*45)
check('Twenty synth trials no failures',len(measure['records'])==20 and all(x['status']=='PASS' for x in measure['records']) and all(sum(x['provider']==p for x in measure['records'])==10 for p in ['flite','piper']))
pairs=load(e/'operator-listening-001.json')['pairs'];check('Five scoped Piper preferences',len(pairs)==5 and all(x[x['verbatim'][0]]=='piper' for x in pairs));check('Unheard first pair preserved','earphones' in load(e/'operator-listening-001.json')['first_short_play'])
check('Neutral freeform reported without equivalence claim',operator['freeform_voice_impression']=='sounded like the one from gemini live' and 'not an equivalence' in operator['scope'])
rows=[json.loads(x) for x in (c/live['run']/'events.jsonl').read_text().splitlines()];check('Live event hash',sha(c/live['run']/'events.jsonl')==live['events_sha256'])
check('Three actual Pi MCP servers',len({x['server_pid'] for x in live['mcp_servers']})==3 and all(x['host']=='Linux' and x['machine']=='aarch64' for x in live['mcp_servers']))
check('Explicit local neural backend',live['configuration'][0]['tts_provider']=='piper' and all(x.get('provider_api','').startswith('local Piper1.8.0;') for x in rows if x['event']=='mcp_request_end' and x['role']=='tts'))
check('Bounded speech requests',live['speech_request_counts']=={'asr':8,'tts':9} and live['matched_speech_request_outcomes'])
for x in live['interruptions']:
    check('Active audible-generation software fence '+str(x['old_generation']),x['active_playout_cancelled'] and x['replacement_completed'] and not x['old_completion_or_replay_events'])
    check('Matching epoch and continued capture '+str(x['replacement_generation']),x['epoch_ack']['epoch']==x['clear']['epoch'] and x['capture_after']['capture_count']>x['clear']['capture_count'])
    check('No acoustic latency inferred '+str(x['replacement_generation']),x['acoustic_stop_latency_ms'] is None)
check('Operator audible replacements/no replay',operator['first_interruption']=='Yes: counting stopped, then four; no counting returned' and operator['second_interruption']=='Yes: stopped, then Paris; no counting returned')
check('Context and task followups completed',{7,8}<=set(live['completed_generations']) and operator['context_followup']=='Yes, correct Paris landmark; no counting' and operator['final_readback']=='Yes, both items were correct and clear; no counting')
startrows=live['task_requests'];check('Actual bounded MCP prepare/commit/read/list',[x['tool'] for x in startrows]==['prepare_item','commit_item','read_item','list_items'])
check('Human confirmation before write/readback',live['human_confirmation'][0]['source']=='direct_current_asr_exact_confirmation' and live['human_confirmation'][0]['monotonic_ns']<startrows[1]['monotonic_ns']<startrows[2]['monotonic_ns']<live['task_verification'][0]['monotonic_ns'])
for name in ['m5-task-baseline-002','task-preconfirmation-001']:check('One row before confirmation '+name,json.loads((c/(name+'.log')).read_text())['row_count']==1)
first=json.loads((c/'task-independent-readback-001.log').read_text());last=json.loads((c/'task-persistence-001.log').read_text());check('Independent SQL durable verified state',all(x['status']=='PASS' and x['row_count']==2 and x['digest_matches_in_agent_readback'] and x['operation_id']==a['verified_task']['operation_id'] for x in [first,last]) and first['state_digest']==last['state_digest'] and first['database_sha256']==last['database_sha256'])
check('Original M4 row preserved',target['original_M4_row_preserved'] and target['new_expected_row_matches'])
check('Independent SQL implementation',(r/'scripts/verify_m4_task.py').read_text().find('from task_mcp')<0 and 'mode=ro' in (r/'scripts/verify_m4_task.py').read_text())
check('No live errors/clean native stop',not live['errors'] and live['session_stop'][0]['cleanup_ok'] and live['session_stop'][0]['worker_finished'] and not target['remaining_speech_task_pids'])
check('No secret/DB import',not any(p.name=='.env' or p.suffix in {'.env','.sqlite3','.db'} for p in c.rglob('*')))
for event in set(x['event'] for x in rows):check('No raw sensitive event payload '+event,all(not {'pcm_b64','audio','text','transcript','token','item','api_key'}&x.keys() for x in rows if x['event']==event))
check('Scoped receipt and open full benchmark',a['status']=='PASS' and 'not full assignment' in a['scope'] and a['requirements']['R7'].startswith('IN_PROGRESS') and a['remaining_limitations'])
for f in ['PROJECT_STATUS.md','EXAM_NOTES.md','README.md','docs/milestone5_voice.md','docs/architecture.md','docs/requirements_traceability.md']:
    p=r/f;text=p.read_text(encoding='utf-8-sig').split('## Historical')[0]
    for link in re.findall(r'\]\(([^)]+)\)',text):
        if '://' not in link and not link.startswith('#'):check('Current link '+f+' '+link,(p.parent/link).exists())
failed=[x for x in checks if not x['pass']];result={'status':'FAIL' if failed else 'PASS','passed':len(checks)-len(failed),'failed':len(failed),'scope':'Read-only artifact reconciliation, not independent perceptual/acoustic test','checks':checks}
(e/'artifact-audit-003.json').write_text(json.dumps(result,indent=2));print(json.dumps({k:v for k,v in result.items() if k!='checks'}));print(json.dumps({'failed_checks':failed}));raise SystemExit(2 if failed else 0)
