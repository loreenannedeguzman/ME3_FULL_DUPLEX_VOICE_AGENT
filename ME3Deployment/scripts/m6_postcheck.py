"""Provider-free post-benchmark builds and exact source/state/process evidence."""
import hashlib,json,platform,subprocess,sys,tarfile
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from benchmark_m6.storage import read,write_new
from benchmark_m6.console import task_state,environment
mode=sys.argv[1];host=platform.system().lower();e=ROOT/'evidence/m6';run=e/'run-20261020-001'
manifest=read(e/'analysis-source-001.json');actual={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in manifest['source_hashes']};assert actual==manifest['source_hashes']
if mode=='build':
    commands=[[sys.executable,'voice_agent.py','--m1','--build-check'],[sys.executable,'voice_agent.py','--build-check'],[sys.executable,'mcp_voice_agent.py','--build-check'],[sys.executable,'m4_voice_agent.py','--build-check'],[sys.executable,'-m','pip','check']]
    if host=='linux':commands.extend([[str(ROOT/'.venv-piper/bin/python'),'-m','pip','check'],['/bin/sh','scripts/run_piper_reuse_voice.sh','--build-check']])
    results=[]
    for i,cmd in enumerate(commands):
        p=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True,timeout=60);log=e/f'{host}-post-build-{i:02}-001.log'
        with log.open('x',encoding='utf-8') as f:f.write(p.stdout+p.stderr)
        results.append({'command':cmd,'native_exit':p.returncode,'log':str(log.relative_to(ROOT))})
    data={'utc':datetime.now(timezone.utc).isoformat(),'analysis_revision':manifest['revision'],'host':platform.uname()._asdict(),'checks':results,'all_pass':all(x['native_exit']==0 for x in results),'hosted_requests':0,'task_writes':0};write_new(e/f'{host}-post-build-001.json',data);print(json.dumps(data,indent=2));assert data['all_pass']
elif mode=='snapshot':
    assert host=='linux';left=[]
    roles=('benchmark_m6.runner','benchmark_m6.py','m4_voice_agent.py','speech_mcp.server','task_mcp.server','piper_worker.py','piper_resident_worker.py')
    for path in Path('/proc').iterdir():
        if not path.name.isdecimal():continue
        try:args=[a.decode(errors='replace') for a in (path/'cmdline').read_bytes().split(b'\0') if a]
        except OSError:continue
        for role in roles:
            if any(a==role or a.endswith('/'+role) for a in args):left.append({'pid':int(path.name),'role':role})
    _,_,state=task_state();end=read(run/'task-final-state.json');data={'utc':datetime.now(timezone.utc).isoformat(),'live_source_revision':read(e/'source-001.json')['revision'],'analysis_source_revision':manifest['revision'],'all84_source_match':True,'source_hashes':actual,'remaining_speech_processes':left,'task_state':state,'matches_after_shutdown':state==end,'environment':environment()};write_new(e/'pi-post-state-001.json',data);print(json.dumps({k:v for k,v in data.items() if k!='source_hashes'},indent=2));assert not left and state==end
elif mode=='export':
    assert host=='linux';files=sorted(p for p in run.rglob('*') if p.is_file());files+=sorted(p for p in e.iterdir() if p.is_file() and (p.name.startswith(('pi-','linux-post-build-')) or p.name=='analysis-source-001.json'))
    assert all(p.suffix in {'.json','.jsonl','.log'} for p in files)
    inventory={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files};out=e/'pi-evidence-inventory-001.json';write_new(out,{'utc':datetime.now(timezone.utc).isoformat(),'files':inventory,'count':len(files),'scope':'metadata/timing/logs only; no DB/audio/transcripts/credentials'});files.append(out)
    archive=ROOT/'.tmp/m6-evidence-001.tar.gz';assert not archive.exists()
    with tarfile.open(archive,'w:gz') as tar:
        for path in files:tar.add(path,arcname=str(path.relative_to(ROOT)),recursive=False)
    print(json.dumps({'archive':str(archive.relative_to(ROOT)),'bytes':archive.stat().st_size,'sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'files':len(files)}))
else:raise ValueError('unknown_mode')
