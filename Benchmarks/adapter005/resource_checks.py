"""Affected finite-budget and existing resource/seam tests only, no providers."""
import argparse,hashlib,io,json,os,socket,sys,unittest
from pathlib import Path
P=Path(__file__).resolve().parent
if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,required=True);parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
 args.out.mkdir(parents=True,exist_ok=False);sys.path[:0]=[str(P),str(args.root)]
 os.environ['ME3_BRIDGE343_OUTPUT']=str(args.out.resolve())
 def denied(*args,**kwargs):raise AssertionError('resource_checks_network_process_forbidden')
 socket.create_connection=denied;socket.socket.connect=denied;socket.socket.connect_ex=denied
 def audit(event,args):
  if event=='subprocess.Popen':raise OSError('resource_checks_subprocess_forbidden')
 sys.addaudithook(audit)
 import test_resources
 stream=io.StringIO();result=unittest.TextTestRunner(stream=stream,verbosity=2,failfast=True).run(unittest.defaultTestLoader.loadTestsFromModule(test_resources))
 (args.out/'tests.txt').write_text(stream.getvalue(),encoding='utf-8');print(stream.getvalue())
 files={name:hashlib.sha256((P/name).read_bytes()).hexdigest() for name in ['benchmark_resources.py','test_resources.py','normal_entry.py','voice-seam-diff.json','overlay/onit_candidate/voice.py','resource_checks.py']}
 receipt=dict(status='PASS' if result.wasSuccessful() else 'STOPPED',tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),skipped=len(result.skipped),files=files,
  scope='FiniteBudget pending-settlement ordering fix; actual owned-scratch contract/state plus retained attenuation/source/seam/one-attempt judge tests. No digital suite repetition.',
  network=0,subprocess=0,ASR=0,reasoning=0,TTS=0,benchmark=False,scoring=False)
 (args.out/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8')
 sys.exit(0 if result.wasSuccessful() else 1)
