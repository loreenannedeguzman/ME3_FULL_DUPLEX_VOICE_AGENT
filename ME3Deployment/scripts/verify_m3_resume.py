"""Audit saved M3 resume evidence; never invokes providers or devices."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,re,shutil,collections
ROOT=Path(__file__).resolve().parents[1];base=ROOT/'evidence/m3';old=base/'pi-copy-005';new=base/'pi-copy-006'
out=base/'m3-checkpoint-002.json';audit=base/'context-audit-001.json'
if out.exists() or audit.exists():raise SystemExit('Preserve prior receipt; choose a new revision')
checks=[]
def check(name,ok,**detail):checks.append({'check':name,'pass':bool(ok),**detail})
def read(p):return p.read_text(encoding='utf-8-sig')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def rows(p):return [json.loads(x) for x in read(p).splitlines()]
def meta(folder,name):return json.loads(read(folder/(name+'.json')))
def one(data,event):return next(x for x in data if x['event']==event)
def load_manifest(p):
 result={}
 for line in read(p).splitlines():
  value,name=line.split(None,1);name=name.strip();target=(ROOT/name).resolve()
  if not target.is_relative_to(ROOT):raise SystemExit('Unsafe manifest path')
  result[name]=value
 return result
manifest=load_manifest(new/'source-hashes-008.sha256')
for name,value in manifest.items():check('deployed_source:'+name,sha(ROOT/name)==value)
for name in ['m3-context-tests-001','m3-context-build-001','m3-context-interruptions-001','m3-context-quota-001','m3-context-text-001','m3-live-fix-protocol-001','m3-live-fix-interruptions-002','m3-live-fix-interruptions-003']:
 check('native_check:'+name,meta(new,name)['exit_code']==0)
check('windows43','Ran 43 tests' in read(base/'windows-context-tests-001.log') and json.loads(read(base/'windows-context-tests-001.json'))['exit_code']==0)
check('pi43','Ran 43 tests' in read(new/'m3-context-tests-001.log'))
check('text_only_recovery','"synthetic_text_recovery": "PASS"' in read(new/'m3-context-text-001.log') and '"tts_requests": 0' in read(new/'m3-context-text-001.log'))
check('provider006_pass',meta(old,'m3-provider-roundtrip-006')['exit_code']==0)
check('live004_failure_retained',meta(old,'m3-live-004')['exit_code']==2)
check('live005_clean_exit_retained',meta(new,'m3-live-005')['exit_code']==0)
check('failed_fixture_retained',meta(new,'m3-live-fix-interruptions-001')['exit_code']==2)
r004=sorted(old.glob('runtime-*/events.jsonl'))[0]
r005=sorted(new.glob('runtime-20261019T120415*/events.jsonl'))[0]
d004=rows(r004);d005=rows(r005)
p006=rows(sorted(old.glob('probe-*/events.jsonl'))[0])
# Raw request inventory, not provider-billed usage or the account's remaining quota.
counts={name:sum(x['event']=='mcp_request_start' and x['role']=='tts' for x in data) for name,data in [('provider006',p006),('live004',d004),('live005',d005)]}
check('request_inventory',counts=={'provider006':1,'live004':5,'live005':3})
check('live005_budget',one(d005,'m3_configuration')['max_human_turns']==3 and one(d005,'session_start')['max_seconds']==480 and one(d005,'session_end')['human_turns']==3)
check('no_new429',not any(x.get('code')==429 for x in d004+d005+p006))
sessions=[]
for label,path,data,scope in [('live004',r004,d004,'PARTIAL: mango memory/recall; interrupted answer incomplete'),('live005',r005,d005,'FAIL: operator hears counting continue/restart, then four')]:
 software=[]
 for cancel in [x for x in data if x['event']=='turn_cancelled' and x.get('pending_work')]:
  oldgen=cancel['generation'];newgen=cancel['replacement_generation']
  starts=[x for x in data if x['event']=='generation_playout_started' and x['generation']==oldgen and x['monotonic_ns']<cancel['monotonic_ns']]
  if not starts:continue
  start=next(x for x in data if x['event']=='local_speech_start' and x['generation']==newgen)
  clear=next(x for x in data if x['event']=='playout_clear' and x['monotonic_ns']>=start['monotonic_ns'])
  ack=next(x for x in data if x['event']=='callback_epoch_observed' and x['epoch']==clear['epoch'])
  capture=next(x for x in data if x['event']=='capture_callback' and x['capture_count']>clear['capture_count'])
  software.append({'old_generation':oldgen,'replacement_generation':newgen,'clear_epoch':clear['epoch'],'discarded_bytes':clear['discarded_bytes'],'callback_epoch_ack':ack['epoch'],'capture_at_clear':clear['capture_count'],'subsequent_capture':capture['capture_count'],'old_generation_completed_after_cancel':any(x['event']=='generation_playout_submitted' and x['generation']==oldgen and x['monotonic_ns']>cancel['monotonic_ns'] for x in data),'scope':'Software control only; does not establish semantic or audible success'})
 sessions.append({'trial':label,'events':str(path.relative_to(ROOT)).replace('\\','/'),'event_sha256':sha(path),'native_exit':meta(old if label=='live004' else new,'m3-live-004' if label=='live004' else 'm3-live-005')['exit_code'],'acceptance':scope,'servers':[x for x in data if x['event']=='mcp_connected'],'session_end':one(data,'session_end'),'session_stop':one(data,'session_stop'),'software_overlap':software})
 check(label+':clean_stop',one(data,'session_stop')['worker_finished'] and one(data,'session_stop')['cleanup_ok'])
state=json.loads(read(new/'context-final-state-001.json'))
check('no_agent_left_running',not state['agent_pids'])
check('reset_conversion',state['next_scheduled_daily_reset_manila']=='2026-10-20T15:00:00+08:00')
m2=json.loads(read(base.parent/'m2/m2-acceptance-001.json'))
for name in ['voice_agent.py','duplex_audio.py']:check('m2_preserved:'+name,sha(ROOT/name)==m2['source_hashes'][name])
check('m1_preserved',sha(base.parent/'m1/voice_agent-accepted.py')=='89d7f71717054545c2b7cf2060c588fe6f7764925ff981d15e3d9d493ce56cc6')
# The pre-fix files remain independently reviewable.
manifest007=load_manifest(new/'source-hashes-007.sha256')
for p in (base/'live-005-source').rglob('*'):
 if p.is_file():
  name=str(p.relative_to(base/'live-005-source')).replace('\\','/')
  check('live005_snapshot:'+name,sha(p)==manifest007[name])
broken=[]
docs=['README.md','PROJECT_STATUS.md','EXAM_NOTES.md','docs/milestone3.md','docs/architecture.md','docs/requirements_traceability.md','docs/offline_work.md','docs/project_status_20261018.md','docs/exam_notes_history_m3_20261018.md','docs/milestone3_20261018.md','docs/architecture_m3_20261018.md','docs/traceability_m3_20261018.md']
for name in docs:
 p=ROOT/name
 for target in re.findall(r'\[[^\]]+\]\(([^)]+)\)',read(p)):
  if '://' in target or target.startswith('#'):continue
  resolved=(p.parent/target.split('#')[0]).resolve()
  if resolved==out.resolve():continue
  if not resolved.exists():broken.append({'document':name,'target':target})
check('document_links',not broken,broken=broken)
check('no_key_pattern_in_new_evidence',not any(re.search(rb'AIza[A-Za-z0-9_-]{30,}',p.read_bytes()) for folder in [old,new] for p in folder.rglob('*') if p.is_file()))
check('git_not_initialized',not (ROOT/'.git').exists())
record={'recorded_utc':datetime.now(timezone.utc).isoformat(),'milestone':3,'status':'BLOCKED','implementation':'PARTIAL_PASS','live_acceptance_met':False,'scope':'Current speech/recall progress; latest context fix has unit/fixture/text-only support, no post-fix audible acceptance.','target':state,'source_hashes':manifest,'source_snapshot':'evidence/m3/context-source-001','prior_receipts':['evidence/m3/m3-checkpoint-001.json','evidence/m3/offline-checkpoint-001.json'],'current_source_not_accepted_live':True,'sessions':sessions,'operator_observations':[{'trial':'live004','verbatim':'Yes, both worked','scope':'mango acknowledgment and correct recall'},{'trial':'live004','verbatim':'Stopped; waiting for four','scope':'short count stop reported; replacement not completed before timeout'},{'trial':'live005','verbatim':'it counted to 10 and said and two plus two is four','scope':'arithmetic correct, counting not abandoned'},{'trial':'live005','verbatim':'It continued or restarted through ten, then said four','scope':'explicit human interruption criterion failure despite native exit0'}],'verification':{'windows_tests':'43/43 PASS','pi_tests':'43/43 PASS','target_build':'PASS','actual_MCP_protocol':'PASS','scripted_tone_cancellation':'PASS; synthetic recognition/text/tones, forced QUIET VAD','quota_fixture':'PASS; synthetic429','synthetic_text_recovery':'PASS; one text-only request, no ASR/TTS','post_fix_audible_recovery':'NOT_VERIFIED'},'budget':{'tts_requests_issued':counts,'total_tts_requests_issued':sum(counts.values()),'operator_reported_model':'Gemini3.8FlashLiteTTS','reported_rpm_limit':3,'reported_rpd_limit':10,'reported_28_day_peak_rpd':'12/10; historical peak, not current balance','remaining_account_quota':'UNKNOWN','max_remaining_own_allocation':1,'new429_today':False,'further_tts_paused':True,'billing_key_project_model_changed_by_agent':False,'next_scheduled_reset_manila':state['next_scheduled_daily_reset_manila'],'automatic_retry':False},'fixes':['Allowlisted internal validation reason without provider text; original long-count ValueError reason unknown.','Validated lower human-turn caps, unchanged max8/default8/45s TTS bound.','Causal epoch/capture synthetic checks replace delayed-log timestamp comparison; failed check preserved.','Unspoken generation-bound canceled/failed history closure preserves user facts and separates actual spoken reply; latest-request instruction.'],'remaining':['Fresh guided test of two audible overlapping stops, correct replacements, context recall/follow-up without restart/old counting.','Exact original long TTS rejection and full ASR/TTS quality/state-of-the-art scoring.','Natural-pause/VAD splitting/noise/echo, acoustic benchmark, independent clean reproduction.','M4 real task/M5 benchmark and actual student mastery.'],'next_trial':{'name':'m3-live-006','executed':False,'max_seconds':480,'max_human_turns':8,'max_tts_requests':9,'wrapper_seconds':510,'repeat_provider_probe':False,'gate':'Sufficient confirmed fresh allowance and operator readiness; same speech backend already passed006.'}}
if all(x['pass'] for x in checks):
 snap=base/'context-source-001';snap.mkdir()
 for name in manifest:
  dest=snap/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,dest)
with out.open('x',encoding='utf-8') as f:json.dump(record,f,indent=2);f.write('\n')
result={'recorded_utc':record['recorded_utc'],'passed':sum(x['pass'] for x in checks),'failed':sum(not x['pass'] for x in checks),'checks':checks,'scope':'Evidence/source/link consistency; preserved failed checks are not promoted to application acceptance.'}
with audit.open('x',encoding='utf-8') as f:json.dump(result,f,indent=2);f.write('\n')
print(json.dumps({'checks_passed':result['passed'],'checks_failed':result['failed'],'failed_checks':[x for x in checks if not x['pass']],'milestone_status':record['status'],'live_acceptance':False,'tts_requests':record['budget']['total_tts_requests_issued']},indent=2))
raise SystemExit(1 if result['failed'] else 0)
