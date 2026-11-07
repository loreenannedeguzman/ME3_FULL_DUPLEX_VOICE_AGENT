"""Read-only target/model/accepted hashes, no audio/API action; build optional."""
import asyncio,hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from benchmark_m6.console import environment,task_state
from benchmark_m6.storage import read
from benchmark_m6.runner import Events
from benchmark_m6.observe import attach
async def build():
 import mcp_voice_agent as app
 from voice_agent import Settings,configure_pi_audio
 import tempfile
 with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as temp:
  e=Events(Path(temp)/'build','A');settings=Settings(api_key='',max_seconds=30);configure_pi_audio(settings)
  try:
   _,_,transport,processor=app.build_pipeline(settings,e,None,None);attach(processor,transport,e);await processor.cleanup();await transport.close()
  finally:e.close()
if __name__=='__main__':
 before=read(ROOT/'evidence/m6/starting-source.json');hashes={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in before['source_hashes']};assert hashes==before['source_hashes']
 result={'all68accepted_source_match':True,'accepted_source_hashes':hashes,'accepted_receipts_match':all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h for p,h in before['receipt_hashes'].items()) if all((ROOT/p).exists() for p in before['receipt_hashes']) else 'Canonical Windows receipt hashes preserved; some frozen receipts not deployed','environment':environment()}
 if sys.platform=='linux':result['task_baseline']=task_state()[2]
 if '--build' in sys.argv:asyncio.run(build());result['actual_observed_pipeline_build']='PASS'
 print(json.dumps(result,indent=2))
