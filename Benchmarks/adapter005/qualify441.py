"""Finite provider-free changed-path gate. No collection/scoring or grants."""
import argparse,hashlib,io,json,os,socket,sys,tempfile,unittest
from pathlib import Path
P=Path(__file__).resolve().parent
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,required=True);parser.add_argument('--course',type=Path,required=True);parser.add_argument('--out',type=Path,required=True);parser.add_argument('--actual-transport',action='store_true');parser.add_argument('--isolation-only',action='store_true');parser.add_argument('--accepted-only',action='store_true');a=parser.parse_args()
 a.out.mkdir(parents=True,exist_ok=False)
 a.out=a.out.resolve();scratch=a.out/'scratch';scratch.mkdir();tempfile.tempdir=str(scratch)
 manifest=json.loads((P/'manifest.DRAFT.json').read_bytes())
 for n,h in manifest['course_hashes'].items():
  if sha(a.course/n)!=h:raise ValueError('pinned_course_changed:'+n)
 if a.actual_transport:
  bindings=json.loads((P/'source-bindings.json').read_bytes())
  for n,row in bindings['files'].items():
   if sha(a.root/n)!=row['sha256']:raise ValueError('current_normal_source_changed:'+n)
 sys.path[:0]=[str(P),str(a.course),str(a.course/'adapters'),str(a.root)]
 os.environ['PYTHONDONTWRITEBYTECODE']='1'
 def denied(*args,**kwargs):raise AssertionError('benchmark441_network_hardware_process_forbidden')
 try:
  import pyaudio
  pyaudio.PyAudio=denied;pyaudio._portaudio.initialize=denied
 except ImportError:pass
 # Imports may load normal trusted native libraries; no instance/device is made.
 import fdbench.marks,fdbench.score.stages,fdbench.queue
 if a.actual_transport:
  import pipecat.frames.frames,pipecat.transports.base_input,pipecat.transports.base_output
  import pipecat.transports.base_transport,pipecat.transports.local.audio
  import pipecat.audio.vad.silero,me3_completion.pipeline,me3_completion.endpoint
 socket.create_connection=denied;socket.socket.connect=denied;socket.socket.connect_ex=denied
 def audit(event,args):
  if event in {'subprocess.Popen','os.system','socket.connect','socket.connect_ex','ctypes.dlopen'}:raise OSError('benchmark441_external_operation_forbidden')
  if event=='open' and isinstance(args[0],(str,bytes,os.PathLike)):
   mode=args[1];flags=args[2]
   writing=isinstance(mode,str) and any(c in mode for c in 'wax+') or type(flags) is int and flags&(os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND)
   if writing and not Path(os.fsdecode(args[0])).resolve().is_relative_to(a.out):raise OSError('benchmark441_owned_output_writes_only')
 sys.addaudithook(audit)
 suite=unittest.TestSuite()
 if a.accepted_only:suite.addTests(unittest.defaultTestLoader.loadTestsFromName('test_accepted441'))
 elif a.isolation_only:suite.addTests(unittest.defaultTestLoader.loadTestsFromName('test_isolation441'))
 else:suite.addTests(unittest.defaultTestLoader.loadTestsFromName('test_changed441'))
 if a.actual_transport and not a.isolation_only and not a.accepted_only:
  suite.addTests(unittest.defaultTestLoader.loadTestsFromName('test_transport441'))
  suite.addTests(unittest.defaultTestLoader.loadTestsFromName('test_isolation441'))
 stream=io.StringIO();result=unittest.TextTestRunner(stream=stream,verbosity=2,failfast=True).run(suite)
 (a.out/'tests.txt').write_text(stream.getvalue(),encoding='utf-8');print(stream.getvalue())
 value=dict(status='BENCHMARK441_CHANGED_PATH_SOFTWARE_PASS' if result.wasSuccessful() else 'STOPPED',tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),skipped=len(result.skipped),actual_current_transport=a.actual_transport,
  course_commit=manifest['course_commit'],source_revision=manifest['source_revision'],manifest_sha256=sha(P/'manifest.DRAFT.json'),
  files={p.relative_to(P).as_posix():sha(p) for p in P.rglob('*.py') if 'baseline' not in p.parts},provider_requests=0,ASR=0,reasoning=0,TTS=0,benchmark_trials=0,scoring=False,
  limitations=['Authored PCM/ASR/model events; no actual providers or benchmark trials.','Actual transport flag identifies current normal pipeline/BaseOutput subset only; warm-floor/semantic/model/native acceptance not repeated.','Wire/SSE route timing and real stage completeness still require new-source actual qualification.'])
 (a.out/'receipt.json').write_text(json.dumps(value,indent=2)+'\n')
 return 0 if result.wasSuccessful() else 1
if __name__=='__main__':raise SystemExit(main())
