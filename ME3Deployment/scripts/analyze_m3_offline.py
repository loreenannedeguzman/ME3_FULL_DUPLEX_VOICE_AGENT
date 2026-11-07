"""Audit offline M3 evidence. Does not invoke an API, audio device or MCP server."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,re,shutil
ROOT=Path(__file__).resolve().parents[1];base=ROOT/'evidence/m3';target=base/'pi-copy-004'
out=base/'offline-checkpoint-001.json'
if out.exists():raise SystemExit('Preserve checkpoint; choose a new audit revision.')
checks=[]
def check(name,ok):checks.append({'check':name,'pass':bool(ok)})
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def rows(prefix):
    path=max(target.glob(prefix+'*'))/'events.jsonl'
    return path,[json.loads(s) for s in path.read_text(encoding='utf-8-sig').splitlines()]
manifest={}
for line in (target/'source-hashes-006.sha256').read_text(encoding='utf-8-sig').splitlines():
    value,name=line.split(None,1);name=name.strip();p=(ROOT/name).resolve()
    if not p.is_relative_to(ROOT):raise SystemExit('Unsafe source path')
    check('target_source:'+name,p.is_file() and sha(p)==value);manifest[name]=value
for name in ['m3-offline-pi-tests-002','m3-offline-build-001','m3-offline-interruptions-002','m3-offline-fault-002','m3-offline-quota-002']:
    check('native_exit:'+name,json.loads((target/(name+'.json')).read_text(encoding='utf-8-sig'))['exit_code']==0)
check('pi_35_tests','Ran 35 tests' in (target/'m3-offline-pi-tests-002.log').read_text(encoding='utf-8-sig'))
check('windows_35_tests','Ran 35 tests' in (base/'windows-offline-tests-002.log').read_text(encoding='utf-8-sig') and json.loads((base/'windows-offline-tests-002.json').read_text(encoding='utf-8-sig'))['exit_code']==0)
path,tone=rows('offline-interruptions-');interruptions=[]
for gen in (2,4):
    source=next(x for x in tone if x['event']=='scripted_interruption' and x['generation']==gen)
    clear=next(x for x in tone if x['event']=='playout_clear' and x['monotonic_ns']>=source['monotonic_ns'])
    ack=next(x for x in tone if x['event']=='callback_epoch_observed' and x['epoch']==clear['epoch'] and x['callback_ns']>=clear['monotonic_ns'])
    capture=next(x for x in tone if x['event']=='capture_callback' and x['capture_count']>clear['capture_count'] and x['callback_ns']>=clear['monotonic_ns'])
    fresh=next(x for x in tone if x['event']=='response_complete' and x['generation']==gen)
    check('old_output_incomplete:'+str(gen-1),not any(x['event']=='generation_playout_submitted' and x.get('generation')==gen-1 for x in tone))
    check('old_output_started:'+str(gen-1),any(x['event']=='generation_playout_started' and x.get('generation')==gen-1 for x in tone))
    check('old_turn_cancelled:'+str(gen-1),any(x['event']=='turn_cancelled' and x.get('generation')==gen-1 and x.get('pending_work') for x in tone))
    interruptions.append({'replacement_generation':gen,'clear_epoch':clear['epoch'],'discarded_bytes':clear['discarded_bytes'],'capture_at_clear':clear['capture_count'],'subsequent_capture':capture['capture_count'],'callback_ack_ns':ack['callback_ns'],'new_output_complete_ns':fresh['monotonic_ns'],'acoustic_stop_latency':'NOT_MEASURED'})
scenarios={}
for prefix,name in [('offline-interruptions-','interruptions'),('offline-fault-','fault'),('quota-fixture-','quota')]:
    p,data=rows(prefix);stop=next(x for x in data if x['event']=='session_stop')
    check(name+':cleanup',stop['worker_finished'] and stop['cleanup_ok'] and any(x['event']=='duplex_transport_closed' for x in data))
    scenarios[name]={'events':str(p.relative_to(ROOT)).replace('\\','/'),'sha256':sha(p),'session_stop':stop,'servers':[x for x in data if x['event']=='mcp_connected']}
    if name=='fault':
        check('fault_observed',any(x['event']=='pipeline_fault' for x in data) and stop['reason']=='pipeline_fault')
        check('fault_payload_not_retained','synthetic-private-frame-canary' not in p.read_text(encoding='utf-8-sig'))
    if name=='quota':check('one_request_and_quota_cue',sum(x['event']=='mcp_request_start' for x in data)==1 and sum(x['event']=='session_provider_limited' for x in data)==1 and stop['reason']=='provider_quota')
old=json.loads((ROOT/'evidence/m2/m2-acceptance-001.json').read_text(encoding='utf-8-sig'))
for name in ('voice_agent.py','duplex_audio.py'):check('m2_preserved:'+name,sha(ROOT/name)==old['source_hashes'][name])
check('failed_quota_regression_preserved',json.loads((target/'m3-offline-quota-001.json').read_text(encoding='utf-8-sig'))['exit_code']==2)
check('evidence_key_pattern_absent',not any(re.search(rb'AIza[A-Za-z0-9_-]{30,}',p.read_bytes()) for p in target.rglob('*') if p.is_file()))
check('git_not_initialized',not (ROOT/'.git').exists())
result={'recorded_utc':datetime.now(timezone.utc).isoformat(),'experiment':'EXP-M3-002','milestone_3_status':'BLOCKED','live_acceptance_met':False,'scope':'Provider-free device/MCP diagnostics; synthetic transcripts/reasoning/tones; VAD forced QUIET for tone/fault tests. No Google calls by these fixtures, no acoustic or speech-quality acceptance.','checks_passed':sum(x['pass'] for x in checks),'checks_failed':sum(not x['pass'] for x in checks),'checks':checks,'source_hashes':manifest,'interruptions':interruptions,'scenarios':scenarios,'preserved_failure':'m3-offline-quota-001 exit2: missing quota-status event, clean worker/audio shutdown; restored event, fresh002 PASS.','remaining_blocker':'Google TTS daily quota; verify availability after expected2026-10-19 15:00Manila reset before one diagnostic and guided live trial. No automatic retry.','prior_checkpoint':'evidence/m3/m3-checkpoint-001.json'}
# Preserve the exact audited implementation independently of future edits.
snapshot=base/'offline-source-002';snapshot.mkdir()
for name in manifest:
    dest=snapshot/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,dest)
with out.open('x',encoding='utf-8') as f:json.dump(result,f,indent=2);f.write('\n')
print(json.dumps({'checks_passed':result['checks_passed'],'checks_failed':result['checks_failed'],'interruptions':interruptions,'milestone_3_status':'BLOCKED'},indent=2))
raise SystemExit(1 if result['checks_failed'] else 0)
