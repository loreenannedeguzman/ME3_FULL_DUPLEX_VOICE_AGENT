"""Read-only evidence/source audit for EXP-M3-005; no API/device calls."""
import hashlib,json,re,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];E=ROOT/'evidence/m3';C=E/'provider-copy-001'
def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
checks=[]
def check(name,value):checks.append({'check':name,'pass':bool(value)})
manifest=read(E/'tts-provider-source-002.json')
for file,digest in manifest['deployed_hashes'].items():
    check('Pi/root source '+file,hashlib.sha256((ROOT/file).read_bytes()).hexdigest()==digest)
    check('Frozen source '+file,hashlib.sha256((E/'tts-provider-source-002'/file).read_bytes()).hexdigest()==digest)
for file,digest in manifest['windows_only_hashes'].items():check('Windows lock '+file,hashlib.sha256((ROOT/file).read_bytes()).hexdigest()==digest)
old=read(E/'m3-checkpoint-002.json')
for file,digest in old['source_hashes'].items():check('Preserved baseline '+file,hashlib.sha256((E/'provider-baseline-001'/file).read_bytes()).hexdigest()==digest)
for file in ('voice_agent.py','duplex_audio.py','speech_mcp/provider.py','speech_mcp/contracts.py','speech_mcp/rate_limit.py','requirements-m3-pi.lock.txt'):
    check('Unchanged accepted/shared '+file,hashlib.sha256((ROOT/file).read_bytes()).hexdigest()==old['source_hashes'][file])
for file in ('requirements-pi.lock.txt','requirements-windows.lock.txt'):
    check('Accepted M2 lock '+file,(ROOT/file).read_bytes()==(ROOT/'evidence/m2'/('accepted-'+file)).read_bytes())
check('Accepted M1 snapshot',hashlib.sha256((ROOT/'evidence/m1/voice_agent-accepted.py').read_bytes()).hexdigest()=='89d7f71717054545c2b7cf2060c588fe6f7764925ff981d15e3d9d493ce56cc6')
for p in C.glob('*.json'):
    data=read(p)
    if 'exit_code' in data:check('Native Pi exit '+p.stem,data['exit_code']==0)
for suffix in ('tests-003','pip-check-001','build-001','m1-build-001','m2-build-001'):
    p=E/('windows-tts-'+suffix+'.json');check('Windows exit '+suffix,read(p)['exit_code']==0)
for p in (E/'windows-tts-tests-003.log',C/'tts-adapters-tests-001.log'):
    text=p.read_text(encoding='utf-8-sig');check('67-test suite '+p.name,'Ran 67 tests' in text and re.search(r'\bOK\b',text) is not None)
receipt=read(E/'m3-acceptance-001.json');operator=read(E/'tts-provider-operator-001.json');analysis=read(E/'tts-live-analysis-001.json')
rows=[json.loads(x) for x in (C/receipt['runtime']/'events.jsonl').read_text().splitlines()]
starts=[x for x in rows if x['event']=='mcp_request_start'];results=[x for x in rows if x['event'] in {'mcp_request_end','mcp_request_cancelled','mcp_request_error'}]
for start in starts:
    matches=[x for x in results if x.get('request_id')==start['request_id']]
    check('Bound request outcome '+start['request_id'],len(matches)==1 and matches[0]['generation']==start['generation'] and matches[0]['role']==start['role'])
check('Explicit actual provider',analysis['configuration'][0]['tts_provider']=='flite' and {x['role']:x['provider'] for x in analysis['mcp_servers']}=={'asr':'google','tts':'flite'})
check('Pi separate speech processes',len({x['server_pid'] for x in analysis['mcp_servers']})==2 and all(x['machine']=='aarch64' for x in analysis['mcp_servers']))
check('Eight ASR budget',sum(x['role']=='asr' for x in starts)==8)
check('Eight local TTS budget',sum(x['role']=='tts' for x in starts)==8 and all(x['provider_api'].startswith('local FFmpeg flite;') for x in results if x['event']=='mcp_request_end' and x['role']=='tts'))
check('Seven reasoning requests',len([x for x in rows if x['event']=='reasoning_start'])==7)
check('No provider/pipeline errors',not analysis['errors'] and not any(x['event'] in {'pipeline_fault','speech_service_failed','session_provider_limited'} for x in rows))
check('Extra segment cap retained',sum(x['event']=='utterance_committed' for x in rows)==9 and sum(x['event']=='turn_budget_reached' for x in rows)==1 and not any(x.get('generation')==9 for x in starts))
for new,old_generation,answer in ((5,4,'four'),(7,6,'paris')):
    clear=next(x for x in analysis['clears'] if x['local_speech_generation']==new)
    check('Active old generation canceled '+str(old_generation),clear['cancelled_generation']==old_generation and clear['pending_work_cancelled'] and not clear['prior_playout_finished_before_clear'])
    check('Epoch and continued capture '+str(new),clear['callback_epoch_acknowledged'] and clear['capture_continues'] and clear['next_capture_count']>clear['capture_at_clear'])
    check('No old output completion '+str(old_generation),not any(x['event'] in {'response_complete','generation_playout_submitted'} and x.get('generation')==old_generation for x in rows))
    check('Replacement complete '+str(new),any(x['event']=='response_complete' and x.get('generation')==new for x in rows))
check('Final contextual answer complete',any(x['event']=='response_complete' and x.get('generation')==8 for x in rows))
check('Operator audible/semantic confirmations',operator['short_mcp_playback']=='Yes, clear; continue' and operator['mango_ack_and_recall']=='Yes, both worked' and operator['count_interrupted_then_four']=='Yes: stopped, then four' and operator['second_count_interrupted_then_paris']=='Yes: stopped, then Paris' and operator['context_landmark_no_count_replay']=='Yes, correct landmark; no counting')
check('Worker/device clean stop',analysis['session_stop'][0]['cleanup_ok'] and analysis['session_stop'][0]['worker_finished'] and len(analysis['transport_close'])==1)
check('No target speech processes',read(C/'tts-target-inventory-001.json')['remaining_project_speech_pids']==[])
check('Earlier failed acceptance remains historical',old['status']=='BLOCKED' and old['live_acceptance_met'] is False)
check('Current acceptance scoped',receipt['status']=='PASS' and 'Not full assignment' in receipt['scope'])
check('No secret files in imported evidence',not any(p.name=='.env' or p.suffix=='.env' for p in C.rglob('*')))
# Check new/current Markdown file links only; anchors and primary web links excluded.
for file in ('PROJECT_STATUS.md','EXAM_NOTES.md','README.md','docs/architecture.md','docs/requirements_traceability.md','docs/milestone3.md'):
    text=(ROOT/file).read_text(encoding='utf-8-sig').split('## Historical pre-adapter')[0]
    for target in re.findall(r'\]\(([^)]+)\)',text):
        if '://' not in target and not target.startswith('#'):check('Current link '+file+' -> '+target,((ROOT/file).parent/target).exists())
failed=[x for x in checks if not x['pass']]
print(json.dumps({'status':'PASS' if not failed else 'FAIL','passed':len(checks)-len(failed),'failed':len(failed),'scope':'Artifact consistency only; operator evidence establishes audibility. No API/device requests.','checks':checks},indent=2))
raise SystemExit(2 if failed else 0)
