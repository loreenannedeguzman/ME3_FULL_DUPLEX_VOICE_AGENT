"""No devices/providers/writes: accepted-source preservation and candidate audit."""
from pathlib import Path
import hashlib,json,sys
r=Path(__file__).resolve().parents[1];e=r/'evidence/m5';start=json.loads((e/'starting-revision-001.json').read_text());checks=[]
def check(name,ok):checks.append({'check':name,'pass':bool(ok)})
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
for f,h in start['source_hashes'].items():check('Frozen M4 source '+f,sha(e/'starting-source'/f)==h)
for f,h in start['accepted_receipts'].items():check('Accepted receipt unchanged '+f,sha(r/f)==h)
allowed={'speech_mcp/tts_config.py','speech_mcp/tts_adapters.py','speech_mcp/client.py','speech_mcp/errors.py','speech_mcp/server.py'}
for f,h in start['source_hashes'].items():
    if f not in allowed:check('Preserved runtime/test/dependency '+f,sha(r/f)==h)
check('M1 source snapshot',sha(r/'evidence/m1/voice_agent-accepted.py')=='89d7f71717054545c2b7cf2060c588fe6f7764925ff981d15e3d9d493ce56cc6')
for f in ('requirements-pi.lock.txt','requirements-windows.lock.txt'):check('M2 dependency snapshot '+f,(r/f).read_bytes()==(r/'evidence/m2'/('accepted-'+f)).read_bytes())
manifest=json.loads((e/'voice-source-001.json').read_text())
for f,h in manifest['source_hashes'].items():check('Candidate hash '+f,sha(r/f)==h)
fail=[x for x in checks if not x['pass']];print(json.dumps({'status':'FAIL' if fail else 'PASS','passed':len(checks)-len(fail),'failed':len(fail),'checks':checks,'scope':'Artifact/code audit only; no live/acoustic/quality inference'},indent=2));raise SystemExit(2 if fail else 0)
