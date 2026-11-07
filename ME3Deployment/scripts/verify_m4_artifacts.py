"""Canonical-project artifact audit for M4. No devices/providers or database writes."""
import hashlib,json,re,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];E=ROOT/'evidence/m4';C=E/'pi-copy-001'
def load(path):return json.loads(path.read_text(encoding='utf-8-sig'))
checks=[]
def check(name,value):checks.append({'check':name,'pass':bool(value)})
a=load(E/'m4-acceptance-001.json');m=load(E/'accepted-source-001.json');start=load(E/'starting-revision-001.json');target=load(C/'target-source-002.json');analysis=load(E/'live-analysis-001.json');operator=load(E/'operator-receipt-001.json')
for f,h in m['source_hashes'].items():
    check('Canonical source '+f,hashlib.sha256((ROOT/f).read_bytes()).hexdigest()==h)
    check('Frozen source '+f,hashlib.sha256((E/'accepted-source-001'/f).read_bytes()).hexdigest()==h)
for f,h in start['hashes'].items():
    check('Starting source '+f,hashlib.sha256((E/'starting-source'/f).read_bytes()).hexdigest()==h)
    check('M3 preserved '+f,hashlib.sha256((ROOT/f).read_bytes()).hexdigest()==h and target['m3_unchanged'][f])
for name,relative,key in [('M2','evidence/m2/m2-acceptance-001.json','m2_receipt_sha256'),('M3','evidence/m3/m3-acceptance-001.json','m3_receipt_sha256')]:check(name+' receipt immutable',hashlib.sha256((ROOT/relative).read_bytes()).hexdigest()==start[key])
check('M1 source immutable',hashlib.sha256((ROOT/'evidence/m1/voice_agent-accepted.py').read_bytes()).hexdigest()=='89d7f71717054545c2b7cf2060c588fe6f7764925ff981d15e3d9d493ce56cc6')
for f in ('requirements-pi.lock.txt','requirements-windows.lock.txt'):check('M2 dependency lock '+f,(ROOT/f).read_bytes()==(ROOT/'evidence/m2'/('accepted-'+f)).read_bytes())
for name in ('task-tests-001','m4-build-001','m3-preservation-build-001','task-baseline-001','task-preconfirmation-state-001','task-independent-readback-001','task-persistence-after-shutdown-001','m4-live-001'):check('Pi native exit '+name,load(C/(name+'.json'))['exit_code']==0)
for name in ('windows-tests-002','windows-build-001','windows-pip-check-001'):check('Windows native exit '+name,load(E/(name+'.json'))['exit_code']==0)
for path in (E/'windows-tests-002.log',C/'task-tests-001.log'):
    text=path.read_text(encoding='utf-8-sig');check('93test result '+path.name,'Ran 93 tests' in text and re.search(r'\bOK\b',text))
check('Failed development run retained',load(E/'windows-tests-001.json')['exit_code']==1 and 'FAILED (errors=22)' in (E/'windows-tests-001.log').read_text(encoding='utf-8-sig'))
check('Derived import failure retained',load(E/'analysis-helper-failure-001.json')['exit_code']==1)
check('M4 runtime unchanged across tests/live',target['m4_unchanged_since_tests'] and all(target['m4_source_hashes'][f]==m['source_hashes'][f] for f in target['m4_source_hashes']))
check('No speech/task processes left',target['remaining_speech_task_pids']==[])
rows=[json.loads(x) for x in (C/a['run']/'events.jsonl').read_text().splitlines()]
for x in rows:
    check('No sensitive payload keys in event '+x['event'],not {'pcm_b64','audio','text','transcript','token','item','api_key'}&x.keys())
# Reduce privacy checks to one result per event type, preserving any failure.
seen={};other=[]
for x in checks:
    if x['check'].startswith('No sensitive payload keys'):
        if x['check'] not in seen:seen[x['check']]=x
        else:seen[x['check']]['pass']=seen[x['check']]['pass'] and x['pass']
    else:other.append(x)
checks=other+list(seen.values())
starts=[x for x in rows if x['event']=='task_mcp_request_start'];ends=[x for x in rows if x['event']=='task_mcp_request_end']
for x in starts:
    matches=[y for y in ends if y['request_id']==x['request_id']]
    check('Task request outcome '+x['request_id'],len(matches)==1 and matches[0]['tool']==x['tool'] and matches[0]['generation']==x['generation'])
check('Exactly one commit',sum(x['tool']=='commit_item' for x in starts)==1 and [x['tool'] for x in starts]==['prepare_item','commit_item','read_item','list_items','list_items'])
check('Direct human confirmation precedes effect',analysis['confirmation']['source']=='direct_current_asr_exact_confirmation' and analysis['confirmation']['monotonic_ns']<starts[1]['monotonic_ns']<starts[2]['monotonic_ns']<analysis['verification']['monotonic_ns'])
check('Separate three Pi MCP processes',len({x['server_pid'] for x in analysis['mcp_servers']})==3 and all(x['machine']=='aarch64' and x['host']=='Linux' for x in analysis['mcp_servers']))
check('Preconfirmation empty',analysis['preconfirmation_state']['row_count']==0 and analysis['baseline']['row_count']==0)
state=analysis['independent_persistence'];first=analysis['independent_first_readback']
check('Independent authoritative state match',state['status']=='PASS' and state['operation_id']==a['operation_id'] and state['row_count']==1 and state['item_matches_expected'] and state['quantity_matches_expected'] and state['digest_matches_in_agent_readback'])
check('State persists after shutdown',first['database_sha256']==state['database_sha256'] and first['state_digest']==state['state_digest'] and state['state_digest']==analysis['verification']['state_digest'])
check('Independent verifier avoids task code imports','from task_mcp' not in (ROOT/'scripts/verify_m4_task.py').read_text() and 'mode=ro' in (ROOT/'scripts/verify_m4_task.py').read_text())
check('Actual task/database not copied',not target['task_database_copied'] and not any(p.suffix in {'.sqlite3','.db'} or p.name=='.env' for p in C.rglob('*')))
check('Operator confirms task and readback',operator['spoken_confirmation_and_result']=='Yes, saved and checked two notebooks' and operator['saved_readback']=='Yes, two notebooks' and operator['readback_after_interruption']=='Yes, two notebooks; no counting')
check('Operator audible interruption',operator['audible_overlap_recovery']=='Yes: stopped, then four; no count returned')
old,new=5,6
check('Old count active at cancellation',any(x['event']=='generation_playout_started' and x.get('generation')==old for x in rows) and not any(x['event'] in {'response_complete','generation_playout_submitted'} and x.get('generation')==old for x in rows))
check('Replacement and later task readback complete',all(any(x['event']=='response_complete' and x.get('generation')==g for x in rows) for g in (6,7)))
check('Epoch ack and continuous capture',analysis['interruption']['epoch_ack']['epoch']==6 and analysis['interruption']['capture_after']['capture_count']>analysis['interruption']['clear']['capture_count'])
check('No task/provider/pipeline errors',not any(x['event'] in {'turn_error','task_failure','task_mcp_request_error','mcp_request_error','pipeline_fault','speech_service_failed'} for x in rows))
check('Request budget retained',sum(x['event']=='mcp_request_start' and x['role']=='asr' for x in rows)==7 and sum(x['event']=='mcp_request_start' and x['role']=='tts' for x in rows)==8 and len(starts)==5)
check('Explicit local TTS path',all(x.get('provider_api','').startswith('local FFmpeg flite;') for x in rows if x['event']=='mcp_request_end' and x['role']=='tts'))
check('Clean shutdown',analysis['session_stop']['cleanup_ok'] and analysis['session_stop']['worker_finished'] and analysis['session_end']['task_failures']==0)
check('Scoped M4 acceptance',a['status']=='PASS' and a['R6']=='PASS' and 'Not full assignment' in a['scope'])
for file in ('PROJECT_STATUS.md','EXAM_NOTES.md','README.md','docs/architecture.md','docs/requirements_traceability.md','docs/milestone4.md'):
    text=(ROOT/file).read_text(encoding='utf-8-sig').split('## Historical accepted M3')[0]
    for link in re.findall(r'\]\(([^)]+)\)',text):
        if '://' not in link and not link.startswith('#'):check('Current link '+file+' -> '+link,((ROOT/file).parent/link).exists())
failures=[x for x in checks if not x['pass']]
print(json.dumps({'status':'PASS' if not failures else 'FAIL','passed':len(checks)-len(failures),'failed':len(failures),'scope':'Read-only artifact/source audit, not independent acoustic measurement; no provider/device/task writes.','checks':checks},indent=2))
raise SystemExit(2 if failures else 0)
