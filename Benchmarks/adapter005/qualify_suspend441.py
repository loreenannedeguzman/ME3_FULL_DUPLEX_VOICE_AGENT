"""One no-network actual BaseOutput/buffer check, supervised and provider-free."""
import argparse,asyncio,hashlib,json,os,sys,traceback,threading
from pathlib import Path
P=Path(__file__).resolve().parent
def main(a):
 out=a.out.resolve();out.mkdir(parents=True,exist_ok=False);os.environ['PYTHONDONTWRITEBYTECODE']='1'
 sys.path[:0]=[str(P),str(a.course),str(a.course/'adapters'),str(a.root)]
 def denied(*args,**kw):raise RuntimeError('suspension_fixture_hardware_forbidden')
 try:
  import pyaudio;pyaudio.PyAudio=denied;pyaudio._portaudio.initialize=denied
 except ImportError:pass
 import pipecat.transports.websocket.client,voice_agent,duplex_audio,digital_transport
 import me3_completion.playback
 assert hashlib.sha256(Path(me3_completion.playback.__file__).read_bytes()).hexdigest()=='4bcd2c49389396371be57278cf5e3f5b720c1c4b0bf042b9c08908d8e6cb01fd'
 from pipecat.audio.utils import create_stream_resampler
 create_stream_resampler(clear_after_secs=None) # Preload native dependency, no audio I/O.
 from test_suspend441 import exercise
 loop=asyncio.SelectorEventLoop()
 def audit(event,args):
  if event in {'subprocess.Popen','os.system','ctypes.dlopen','socket.connect','socket.connect_ex','socket.getaddrinfo','socket.bind'}:raise OSError('suspension_fixture_no_network_process_device')
  if event=='open' and isinstance(args[0],(str,bytes,os.PathLike)):
   mode,flags=args[1:3];writing=isinstance(mode,str) and any(c in mode for c in 'wax+') or type(flags) is int and flags&(os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND)
   if writing and not Path(os.fsdecode(args[0])).resolve().is_relative_to(out):raise OSError('suspension_fixture_owned_output_only')
 sys.addaudithook(audit);failure=None;value={}
 def expired():os._exit(124)
 watchdog=threading.Timer(35,expired);watchdog.daemon=True;watchdog.start()
 try:
  with asyncio.Runner(loop_factory=lambda:loop) as runner:value=runner.run(asyncio.wait_for(exercise(out),25))
 except BaseException as exc:failure=type(exc).__name__; (out/'exception.txt').write_text(traceback.format_exc(),encoding='utf-8')
 finally:watchdog.cancel()
 value.update(status='BENCHMARK441_CHANGED_PATH_SOFTWARE_PASS' if failure is None else 'STOPPED',failure=failure,tests=1,failures=int(failure is not None),errors=0,skipped=0,actual_current_transport=True,provider_requests=0,benchmark_trials=0,network_connections=0,normal_production_changed=False,scope='Actual normal SuspendableBuffer/PlayoutBuffer, actual Pipecat BaseOutput.MediaSender and external DigitalOutput/Core; authored wire/error observer, no pipeline/providers/devices.')
 (out/'receipt.json').write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8');print(json.dumps(value));return failure is not None
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--course',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--actual-transport',action='store_true');p.add_argument('--accepted-only',action='store_true');raise SystemExit(main(p.parse_args()))
