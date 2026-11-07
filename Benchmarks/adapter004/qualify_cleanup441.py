"""One focused owned-loopback cleanup case; no providers/devices/subprocesses."""
import argparse,asyncio,hashlib,json,os,socket,sys,traceback,threading
from pathlib import Path
P=Path(__file__).resolve().parent
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def main(a):
 out=a.out.resolve();out.mkdir(parents=True,exist_ok=False);os.environ['PYTHONDONTWRITEBYTECODE']='1'
 sys.path[:0]=[str(P),str(a.course),str(a.course/'adapters'),str(a.root)]
 def denied(*args,**kw):raise RuntimeError('cleanup_fixture_hardware_forbidden')
 try:
  import pyaudio;pyaudio.PyAudio=denied;pyaudio._portaudio.initialize=denied
 except ImportError:pass
 import pipecat.transports.websocket.client,pipecat.utils.asyncio.task_manager
 import voice_agent,duplex_audio,launch_assistant,normal_entry,digital_transport,pipecat_fdbench,fdbench_marks
 from test_cleanup441 import exercise
 loop=asyncio.SelectorEventLoop() # Own self-pipe before the three-connection audit.
 ports=set();connections=[]
 def allow_port(port):
  assert type(port) is int and port>0;ports.add(port)
 def audit(event,args):
  if event in {'subprocess.Popen','os.system','ctypes.dlopen'}:raise OSError('cleanup_fixture_external_operation_forbidden')
  if event in {'socket.connect','socket.connect_ex'}:
   addr=args[1]
   if not isinstance(addr,tuple) or addr[0]!='127.0.0.1' or addr[1] not in ports or len(connections)>=3:raise OSError('cleanup_owned_loopback_only')
   connections.append(addr)
  if event=='socket.bind' and (not isinstance(args[1],tuple) or args[1][:2]!=('127.0.0.1',0)):raise OSError('cleanup_owned_ephemeral_listener_only')
  if event=='socket.getaddrinfo' and args[0]!='127.0.0.1':raise OSError('cleanup_external_DNS_forbidden')
  if event=='open' and isinstance(args[0],(str,bytes,os.PathLike)):
   mode,flags=args[1:3];writing=isinstance(mode,str) and any(c in mode for c in 'wax+') or type(flags) is int and flags&(os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND)
   if writing and not Path(os.fsdecode(args[0])).resolve().is_relative_to(out):raise OSError('cleanup_fixture_owned_output_only')
 sys.addaudithook(audit);failure=None;value={}
 def expired():
  (out/'watchdog-stop.json').write_text(json.dumps(dict(status='STOPPED_FIXTURE_DEADLINE',provider_requests=0,settlement_claimed=False))+'\n',encoding='utf-8');os._exit(124)
 watchdog=threading.Timer(40,expired);watchdog.daemon=True;watchdog.start()
 try:
  with asyncio.Runner(loop_factory=lambda:loop) as runner:value=runner.run(asyncio.wait_for(exercise(out,allow_port),30))
 except BaseException as exc:failure=type(exc).__name__; (out/'exception.txt').write_text(traceback.format_exc(),encoding='utf-8')
 finally:watchdog.cancel()
 value.update(status='BENCHMARK441_CHANGED_PATH_SOFTWARE_PASS' if failure is None else 'STOPPED',failure=failure,tests=1,failures=int(failure is not None),errors=0,skipped=0,actual_current_transport=True,provider_requests=0,benchmark_trials=0,loopback_connections=len(connections),normal_production_changed=False,scope='Actual installed Pipecat Session/TaskManager and pinned upstream launcher; authored normal startup and maliciously unresponsive owned loopback peer. Historical pending step remains unproved.')
 (out/'receipt.json').write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8');print(json.dumps(value));return failure is not None
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--course',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--actual-transport',action='store_true');p.add_argument('--accepted-only',action='store_true');raise SystemExit(main(p.parse_args()))
