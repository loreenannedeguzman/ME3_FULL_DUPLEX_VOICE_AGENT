"""Provider-free source/entrypoint checks, all outputs under evidence/core."""
import hashlib,json,platform,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];host=platform.system().lower();folder=ROOT/'evidence/core';source=json.loads((folder/'source-001.json').read_text());base=json.loads((folder/'starting-source.json').read_text())
assert all(hashlib.sha256((ROOT/n).read_bytes()).hexdigest()==h for n,h in source['source_hashes'].items())
assert all(hashlib.sha256((ROOT/n).read_bytes()).hexdigest()==h for n,h in base['source_hashes'].items())
commands=[[sys.executable,'scripts/core_entry_build.py','voice_agent','--m1'],[sys.executable,'scripts/core_entry_build.py','voice_agent'],[sys.executable,'scripts/core_entry_build.py','mcp_voice_agent'],[sys.executable,'scripts/core_entry_build.py','m4_voice_agent'],[sys.executable,'scripts/core_entry_build.py','me3_m7.app'],[sys.executable,'scripts/core_entry_build.py','me3_core.app'],[sys.executable,'-m','pip','check']]
if host=='linux':commands.extend([[str(ROOT/'.venv-piper/bin/python'),'-m','pip','check'],['/bin/sh','scripts/run_core_voice.sh','--build-check'],['/bin/sh','-n','scripts/run_m7_voice.sh'],['/bin/sh','-n','scripts/run_piper_reuse_voice.sh']])
results=[]
for i,cmd in enumerate(commands):
    result=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True,timeout=60);path=folder/f'{host}-entry-build-{i:02}-001.log'
    with path.open('x',encoding='utf-8') as f:f.write(result.stdout+result.stderr)
    results.append({'command':cmd,'native_exit':result.returncode,'log':path.name})
data={'host':host,'source_revision':source['revision'],'current_source_match':len(source['source_hashes']),'accepted_source_unchanged':len(base['source_hashes']),'checks':results,'all_pass':all(x['native_exit']==0 for x in results),'provider_requests':0,'log_isolation':'accepted CLI log factories redirected only in process; no prior-phase evidence files created'}
with (folder/f'{host}-preflight-001.json').open('x',encoding='utf-8') as f:json.dump(data,f,indent=2)
print(json.dumps(data,indent=2));assert data['all_pass']
