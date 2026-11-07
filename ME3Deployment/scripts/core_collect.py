"""Post-live metadata collection only; no provider, playback, deployment or DB write."""
import hashlib,json,platform,subprocess,tarfile,os,sys
from pathlib import Path
from datetime import datetime,timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from benchmark_m6.console import task_state,environment
from dotenv import dotenv_values
ROOT=Path(__file__).resolve().parents[1]
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def verify(files):
    bad=[n for n,h in files.items() if not (ROOT/n).is_file() or digest(ROOT/n)!=h]
    return {'expected':len(files),'matched':len(files)-len(bad),'mismatches':bad}
def main():
    source=json.loads((ROOT/'evidence/core/source-003.json').read_text());base=json.loads((ROOT/'evidence/core/starting-source.json').read_text());run=ROOT/'evidence/core/core-20261020-001'
    result={'observed_utc':datetime.now(timezone.utc).isoformat(),'source_revision':source['revision'],'current_source':verify(source['source_hashes']),'accepted_source':verify(base['source_hashes']),'environment':environment(),'collector_sha256':digest(Path(__file__)),'no_provider_calls':True}
    maps=json.loads((ROOT/'evidence/core/target-preservation-baseline.json').read_text());result['prior_target_evidence']={k:verify(v) for k,v in maps.items()}
    relevant=[]
    for p in Path('/proc').iterdir():
        if not p.name.isdigit():continue
        try:args=(p/'cmdline').read_bytes().split(b'\x00');tokens=[x.decode(errors='replace') for x in args]
        except OSError:continue
        modules={'me3_core.live_runner','me3_core.server','me3_m7.server','speech_mcp.server','task_mcp.server'}
        found=next((x for x in tokens if x in modules or x.endswith('/piper_worker.py')),None)
        if found:relevant.append({'pid':int(p.name),'component':found})
    result['remaining_speech_processes']=relevant
    _,_,state=task_state();result['final_task_state']=state
    out=ROOT/'evidence/core/pi-final-audit-001.json'
    with out.open('x',encoding='utf-8') as f:json.dump(result,f,indent=2)
    # Explicit metadata-only export; frozen source/docs live separately onWindows.
    selected=[]
    for p in (ROOT/'evidence/core').rglob('*'):
        rel=p.relative_to(ROOT/'evidence/core')
        if not p.is_file() or p.is_symlink() or any(x.startswith('source-') or x=='documentation-before' for x in rel.parts[:-1]):continue
        if p.suffix not in {'.json','.jsonl','.log'}:continue
        selected.append(p)
    key=dotenv_values(ROOT/'.env',encoding='utf-8-sig').get('GOOGLE_API_KEY','')
    if key and len(key)>15:
        for p in selected:
            if key.encode() in p.read_bytes():raise ValueError('credential_found_export_stopped')
    inventory={p.relative_to(ROOT).as_posix():digest(p) for p in sorted(selected)}
    inv=ROOT/'evidence/core/pi-export-inventory-001.json'
    with inv.open('x',encoding='utf-8') as f:json.dump({'files':inventory,'raw_microphone_or_database_exported':False},f,indent=2)
    target=ROOT/'.tmp/core-evidence-001.tar.gz'
    with tarfile.open(target,'w:gz') as tar:
        for p in selected+[inv]:tar.add(p,arcname=p.relative_to(ROOT).as_posix())
    print(json.dumps({'audit':result,'metadata_files_exported':len(selected)+1,'archive_bytes':target.stat().st_size},indent=2))
    if result['current_source']['mismatches'] or result['accepted_source']['mismatches'] or relevant:raise SystemExit(1)
if __name__=='__main__':main()
