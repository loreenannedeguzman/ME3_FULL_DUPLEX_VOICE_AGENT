"""Check M2 source/evidence/document consistency, without provider or audio access."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib,json,re
root=Path(__file__).resolve().parents[1]
folder=root/'evidence/m2'
output=folder/'artifact-check-001.json'
if output.exists(): raise SystemExit('Preserve prior evidence; choose a new output name.')
checks=[]
def check(name,value,**detail): checks.append({'check':name,'pass':bool(value),**detail})
required=['voice_agent.py','duplex_audio.py','tests/test_duplex_audio.py','scripts/analyze_duplex.py','requirements.txt','requirements-pi.lock.txt','requirements-windows.lock.txt','config/asound-pi.conf','docs/milestone2.md','evidence/m2/m2-acceptance-001.json','evidence/m1/voice_agent-accepted.py']
for p in required: check('artifact:'+p,(root/p).is_file() and (root/p).stat().st_size>0)
receipt=json.loads((folder/'m2-acceptance-001.json').read_text())
for p,expected in receipt['source_hashes'].items(): check('accepted_source_hash:'+p,hashlib.sha256((root/p).read_bytes()).hexdigest()==expected)
raw=folder/'pi-copy-001'/receipt['run']/'events.jsonl'
check('accepted_raw_event_hash',hashlib.sha256(raw.read_bytes()).hexdigest()==receipt['raw_event_sha256'])
check('m1_source_snapshot',hashlib.sha256((root/'evidence/m1/voice_agent-accepted.py').read_bytes()).hexdigest()=='89d7f71717054545c2b7cf2060c588fe6f7764925ff981d15e3d9d493ce56cc6')
for p in ['m2-live-001','m2-pi-unit-001','m2-pi-build-001','m2-pi-scripted-001','m2-m1-fallback-build-001']:
    meta=json.loads((folder/'pi-copy-001'/(p+'.json')).read_text())
    check('pi_native_exit:'+p,meta['exit_code']==0)
broken=[]
for rel in ['README.md','PROJECT_STATUS.md','EXAM_NOTES.md','docs/exam_notes_history.md','docs/architecture.md','docs/requirements_traceability.md','docs/milestone2.md']:
    p=root/rel
    for link in re.findall(r'\[[^\]]+\]\(([^)]+)\)',p.read_text(encoding='utf-8-sig')):
        if '://' in link or link.startswith('#'): continue
        if not (p.parent/link.split('#')[0]).exists(): broken.append({'file':rel,'target':link})
check('local_document_links',not broken,broken=broken)
check('git_not_initialized',not (root/'.git').exists())
check('acceptance_scoped_to_two_overlap_events',receipt['status']=='PASS' and len(receipt['overlap_cancellations'])==2)
result={'timestamp':datetime.now(timezone.utc).isoformat(),'passed':sum(x['pass'] for x in checks),'failed':sum(not x['pass'] for x in checks),'checks':checks,'scope':'Local artifact/record consistency only; complements, does not replace, target live evidence. Same agent implemented and checked.'}
output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
print(json.dumps(result,indent=2))
raise SystemExit(1 if result['failed'] else 0)
