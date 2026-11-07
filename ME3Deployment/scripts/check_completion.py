"""Run checks in new evidence folders; never overwrite accepted phase outputs."""
import json,platform,re,subprocess,sys,time,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def main():
    name,*args=sys.argv[1:]
    if not re.fullmatch(r'[a-z0-9-]+',name):raise ValueError('invalid_check_name')
    folder=ROOT/'evidence/completion/final-20261020-001';folder.mkdir(parents=True,exist_ok=True)
    claim=folder/(name+'.claim.json')
    with claim.open('x') as f:json.dump({'command':[sys.executable,*args],'started_ns':time.monotonic_ns()},f)
    baseline=json.loads((ROOT/'evidence/completion/final-20261020-001/starting-source.json').read_text())
    names=set(baseline['source_hashes'])
    names.update(p.relative_to(ROOT).as_posix() for p in (ROOT/'me3_completion').glob('*.py'))
    names.update(p.relative_to(ROOT).as_posix() for p in (ROOT/'tests').glob('*completion*.py'))
    names.update(p.relative_to(ROOT).as_posix() for p in (ROOT/'scripts').glob('*completion*.py'))
    names.add('scripts/run_completion_voice.sh')
    hashes={n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in sorted(names)}
    revision=hashlib.sha256(json.dumps(hashes,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    with (folder/(name+'.source.json')).open('x') as f:json.dump({'revision':revision,'source_hashes':hashes,'method':'SHA256 of sorted compact JSON name-to-SHA256 map'},f,indent=2)
    start=time.monotonic_ns()
    try:
        p=subprocess.run([sys.executable,*args],cwd=ROOT,capture_output=True,text=True,timeout=360);output=p.stdout+p.stderr;code=p.returncode
    except subprocess.TimeoutExpired as exc:
        def text(x):return x.decode(errors='replace') if isinstance(x,bytes) else x or ''
        output=text(exc.stdout)+text(exc.stderr)+'\nCHECK TIMEOUT';code=124
    with (folder/(name+'.log')).open('x',encoding='utf-8') as f:f.write(output)
    result={'command':[sys.executable,*args],'native_exit':code,'host':platform.system(),'duration_seconds':(time.monotonic_ns()-start)/1e9,'log':name+'.log','provider_requests':0,'source_revision':revision,'scope':'provider-free check; no acoustic success claim'}
    with (folder/(name+'.json')).open('x') as f:json.dump(result,f,indent=2)
    print(output[-7000:]);print(json.dumps(result));return code
if __name__=='__main__':raise SystemExit(main())
