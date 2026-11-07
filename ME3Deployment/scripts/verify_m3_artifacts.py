"""M3 checkpoint/audit: artifact consistency, not live acceptance."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,re
ROOT=Path(__file__).resolve().parents[1];base=ROOT/'evidence/m3';target=base/'pi-copy-003'
output=base/'artifact-check-001.json';receipt_path=base/'m3-checkpoint-001.json'
if output.exists() or receipt_path.exists():raise SystemExit('Preserve prior checkpoint; choose a new output name.')
checks=[]
def check(name,passed,**detail):checks.append({'check':name,'pass':bool(passed),**detail})
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def meta(name):return json.loads((target/(name+'.json')).read_text())
manifest={}
for line in (target/'source-hashes-004.sha256').read_text().splitlines():
    value,rel=line.split(None,1);rel=rel.strip()
    path=(ROOT/rel).resolve()
    if not path.is_relative_to(ROOT):raise SystemExit('Unsafe manifest path')
    manifest[rel]=value
    check('deployed_source_hash:'+rel,path.is_file() and sha(path)==value)
for rel in ['mcp_voice_agent.py','speech_mcp/client.py','speech_mcp/server.py','speech_mcp/controller.py','speech_mcp/contracts.py','speech_mcp/provider.py','speech_mcp/rate_limit.py','tests/test_m3_speech.py','scripts/probe_m3.py','scripts/probe_m3_quota.py','scripts/analyze_m3.py','requirements-m3.txt','requirements-m3-pi.lock.txt','requirements-m3-windows.lock.txt','config/m3.env.example','docs/milestone3.md']:
    check('artifact:'+rel,(ROOT/rel).is_file() and (ROOT/rel).stat().st_size>0)
for name in ['m3-pi-tests-006','m3-final-build-003','m3-scripted-quota-stop-002','m3-protocol-001','m3-provider-roundtrip-003','m3-provider-roundtrip-004']:
    check('native_exit_zero:'+name,meta(name)['exit_code']==0)
check('pi_unit_count_26','Ran 26 tests' in (target/'m3-pi-tests-006.log').read_text())
check('windows_final_unit',json.loads((base/'windows-tests-006.json').read_text())['exit_code']==0 and 'Ran 26 tests' in (base/'windows-tests-006.log').read_text())
check('actual_synthetic_quota_stop','"scripted_quota_stop": "PASS"' in (target/'m3-scripted-quota-stop-002.log').read_text())
old=json.loads((ROOT/'evidence/m2/m2-acceptance-001.json').read_text())
for rel in ['voice_agent.py','duplex_audio.py']:
    check('preserved_m2_source:'+rel,sha(ROOT/rel)==old['source_hashes'][rel] and sha(ROOT/'evidence/m2'/('accepted-'+rel))==old['source_hashes'][rel])
check('preserved_m1_source',sha(ROOT/'evidence/m1/voice_agent-accepted.py')=='89d7f71717054545c2b7cf2060c588fe6f7764925ff981d15e3d9d493ce56cc6')
sessions=[]
runs=['runtime-20261018T103535-5826684506770','runtime-20261018T103817-5988175599088','runtime-20261018T104417-6348862062603']
for number,run in enumerate(runs,1):
    source=target/run/'events.jsonl';rows=[json.loads(x) for x in source.read_text().splitlines()]
    check('preserved_failed_live_'+str(number),meta('m3-live-'+str(number).zfill(3))['exit_code']==2)
    sessions.append({'trial':'m3-live-'+str(number).zfill(3),'runtime':run,'native_exit':2,'raw_event_sha256':sha(source),'mcp_servers':[x for x in rows if x['event']=='mcp_connected'],'session_end':[x for x in rows if x['event']=='session_end'],'errors':[x for x in rows if x['event'] in {'turn_error','mcp_request_error'}],'completed_non_notice_pcm_submissions':sum(x['event']=='response_complete' and not x.get('greeting') and not x.get('error_notice') for x in rows)})
check('reset_timezone_conversion',(target/'next-daily-reset-001.txt').read_text().strip()=='2026-10-19T15:00:00+08:00')
broken=[]
for rel in ['README.md','PROJECT_STATUS.md','EXAM_NOTES.md','docs/architecture.md','docs/requirements_traceability.md','docs/milestone3.md','docs/exam_notes_history_m2.md','docs/exam_notes_history.md']:
    p=ROOT/rel
    for link in re.findall(r'\[[^\]]+\]\(([^)]+)\)',p.read_text(encoding='utf-8-sig')):
        if '://' in link or link.startswith('#'):continue
        if link=='evidence/m3/m3-checkpoint-001.json' or link=='../evidence/m3/m3-checkpoint-001.json':continue # Written below in this same checkpoint.
        if not (p.parent/link.split('#')[0]).exists():broken.append({'file':rel,'target':link})
check('local_document_links',not broken,broken=broken)
secret_hits=[]
for p in base.rglob('*'):
    if p.is_file() and p.suffix in {'.json','.jsonl','.log','.sha256'} and re.search(rb'AIza[A-Za-z0-9_-]{30,}',p.read_bytes()):secret_hits.append(str(p.relative_to(ROOT)))
check('evidence_key_pattern_absent',not secret_hits,files=secret_hits)
check('git_not_initialized',not (ROOT/'.git').exists())
receipt={
 'milestone':3,'status':'BLOCKED','implementation':'PARTIAL_PASS','live_acceptance_met':False,
 'recorded_at_utc':datetime.now(timezone.utc).isoformat(),
 'criterion':'EXP-M3-001: both actual Pi speech MCP servers in live path, coherent repeated turns/context recall and two audible interruptions/new answers/follow-up without restart or old output; code/diagnostics do not replace this.',
 'target':{'model':'Raspberry Pi5 ModelB Rev1.1','os':'Debian13 trixie aarch64','python':'3.13.5','pipecat':'1.12.0','mcp':'1.26.0','google_genai':'2.31.0','audio':'USB mic,HDMI0/LCD earphones'},
 'latest_configuration':{'asr_model':'gemini-3.5-flash-lite','text_model':'gemini-3.5-flash-lite','tts_model':'gemini-3.8-flash-lite-tts','tts_voice':'Kore','tts_api':'GenerateContent','vad_stop_seconds':0.7,'tts_rate_limit':'3 requests/61 seconds; timestamp-only state, one agent','next_live_bound_seconds':240,'max_human_utterances':8},
 'blocker':{'provider':'Google TTS','observed_http_code':429,'trial':'m3-provider-roundtrip-005','quota_source':'Operator AI Studio table in this chat','daily_used':12,'daily_limit':10,'rpm_limit':3,'tpm_used':22,'tpm_limit':10000,'project_tier':'Free; both reported keys same project','next_scheduled_reset_manila':'2026-10-19T15:00:00+08:00','reset_basis':'Google per-project daily quota rule/Pacific midnight + actual Pi IANA timezone conversion; check actual allowance before use','further_generation_after_429':False,'billing_changed':False,'automatic_retry_scheduled':False},
 'verification':{'windows_offline':'26/26 PASS','pi_offline':'26/26 PASS','actual_pi_build':'PASS','actual_pi_synthetic_quota_stop':'PASS; no Google call/live speech','real_mcp_protocol':'PASS','synthetic_speech_roundtrips_003_004':'PASS using earlier Interactions TTS','latest_tts_endpoint_provider_validation':'BLOCKED429','live_context_recall':'NOT_VERIFIED','m3_two_audible_interruptions':'NOT_VERIFIED'},
 'operator_observations':[{'trial':'m3-live-002','verbatim':'She said she could not complete it','interpretation':'Error notice, not a coherent answer; repeated request also produced notice.'},{'trial':'m3-live-003','verbatim':'Yes, she acknowledged it','interpretation':'Normal mango acknowledgment confirmed.'},{'trial':'m3-live-003','verbatim':'No reply or error notice','interpretation':'Recall not confirmed; trace shows TTS timeout.'}],
 'live_sessions':sessions,'latest_deployed_source_hashes':manifest,'source_scope':'Current deployment/offline checkpoint only; current TTS endpoint/VAD/pacing/guards were revised after earlier live outputs and have no accepted live M3 trial.',
 'limitations':['Earlier three software-completed non-greeting PCM submissions are not three human-confirmed coherent replies.','Seven clears and one pending-ASR cancellation are not two controlled audible barge-ins.','No raw speech/transcripts retained; acoustic observation comes from operator.','Callback/software timing is not acoustic latency.','Provider failures/timeouts/native enumeration warnings preserved; no noise/echo/quality or held-out benchmark.','No independently verified real task/M4, standardized benchmark/M5 or student mastery.','Same agent authored and checked; independent clean reproduction/review absent.'],
 'evidence_directory':'evidence/m3/pi-copy-003','m1_m2_acceptance':'Preserved; M2 source/transport unchanged, shared venv gained MCP dependencies; no post-install M2 live rerun.'
}
receipt_path.write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8')
result={'timestamp':datetime.now(timezone.utc).isoformat(),'passed':sum(x['pass'] for x in checks),'failed':sum(not x['pass'] for x in checks),'checks':checks,'scope':'Local source/evidence/record consistency only, not M3 live acceptance. Native failed-trial preservation checks assess records, not application success.'}
output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'status':receipt['status'],'checks_passed':result['passed'],'checks_failed':result['failed'],'failed_checks':[x for x in checks if not x['pass']]},indent=2))
raise SystemExit(1 if result['failed'] else 0)
