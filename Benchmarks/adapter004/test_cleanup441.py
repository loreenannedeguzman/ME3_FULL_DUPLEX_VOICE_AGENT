"""Actual course launcher/Pipecat session/TaskManager, owned loopback peer.

The peer withholds Close acknowledgement to expose the10s/2s bound mismatch.
Normal provider startup/readiness is authored; no model, MCP, audio or accounts.
"""
import asyncio,base64,hashlib,importlib.util,json,struct,time,traceback
from pathlib import Path
from types import SimpleNamespace as NS
P=Path(__file__).resolve().parent

async def exercise(out,allow_port):
 from voice_agent import Settings
 from pipecat.utils.asyncio.task_manager import TaskManager
 from normal_entry import Session
 import launch_assistant as launch
 import digital_transport as candidate
 spec=importlib.util.spec_from_file_location('baseline003_digital_transport',P/'baseline003/digital_transport.py');old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
 class Events:
  def __init__(self):self.rows=[]
  def emit(self,event,**fields):self.rows.append(dict(event=event,**fields))
 async def one(cls,resistant=False):
  def progress(stage):print(cls.__module__,resistant,stage,flush=True)
  progress('begin')
  events=Events();manager=TaskManager();ready=asyncio.Event();startup_observed=asyncio.Event();peer_done=asyncio.Event();release_receiver=asyncio.Event();metadata={};wire=[];connections=[];writers=[];handler_tasks=[];owned=[]
  async def frame(reader):
   head=await reader.readexactly(2);length=head[1]&127
   if length==126:length=int.from_bytes(await reader.readexactly(2),'big')
   elif length==127:length=int.from_bytes(await reader.readexactly(8),'big')
   assert length<=65536
   mask=await reader.readexactly(4) if head[1]&128 else b'';data=await reader.readexactly(length)
   if mask:data=bytes(b^mask[i%4] for i,b in enumerate(data))
   return head[0]&15,data
  async def peer(reader,writer):
   handler_tasks.append(asyncio.current_task());writers.append(writer)
   try:
    head=await reader.readuntil(b'\r\n\r\n');assert len(head)<=16384
    key=next(line.split(b':',1)[1].strip() for line in head.split(b'\r\n') if line.lower().startswith(b'sec-websocket-key:'))
    accept=base64.b64encode(hashlib.sha1(key+b'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest())
    writer.write(b'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: '+accept+b'\r\n\r\n');await writer.drain()
    progress('upgrade_sent');await ready.wait();await startup_observed.wait();bridge=owned[0].resource.bridge
    payload=json.dumps(dict(event='me3_end_session',session=bridge.observation_session,**bridge.ready_metadata)).encode()
    writer.write(b'\x81'+(bytes([len(payload)]) if len(payload)<126 else b'\x7e'+struct.pack('!H',len(payload)))+payload);await writer.drain()
    while True:
     opcode,data=await frame(reader)
     if opcode==1:wire.append(json.loads(data))
     elif opcode==8:
      metadata['peer_close_received']=True
      # Deliberately no Close reply. Public client close_timeout must abort TCP.
      await reader.read();break
   except (asyncio.IncompleteReadError,ConnectionResetError):pass
   finally:
    writer.close();await writer.wait_closed();peer_done.set()
  server=await asyncio.start_server(peer,'127.0.0.1',0);port=server.sockets[0].getsockname()[1];allow_port(port)
  progress('server_ready')
  class Entry:
   def audio_rates(self):return 16000,48000
   async def build(self,transport,log_dir):
    bridge=cls(transport,Settings(api_key='',output_rate=24000,output_channels=1),events,ready_metadata=dict(source_revision='f'*64,contract_sha256='a'*64))
    class Processor:
     @property
     def candidate_listening(self):startup_observed.set();return True
    stop=asyncio.Event();resource=NS(bridge=bridge,processor=Processor())
    async def start():
     progress('acquiring')
     await asyncio.gather(bridge.acquire('input',manager),bridge.acquire('output',manager))
     connections.append(bridge.session._websocket)
     bridge.ready.set();bridge.core.mark_listening_ready();ready.set()
     progress('ready')
     await stop.wait()
     if resistant:
      await manager.cancel_task(bridge.session._client_task)
      async def resist():
       while not release_receiver.is_set():
        try:await release_receiver.wait()
        except asyncio.CancelledError:pass
      bridge.session._client_task=manager.create_task(resist(),'authored_cancellation_resistant_receiver');await asyncio.sleep(0)
     progress('closing');await bridge.close();progress('closed')
    session=Session(start,stop,resource);owned.append(session)
    return session._run(),session.cancel
  started=time.monotonic();failure=None;running=asyncio.create_task(launch.run_once('ws://127.0.0.1:'+str(port)+'/?out_sr=48000',Entry(),out,False,stats_hz=0,marks=False))
  try:
   await asyncio.wait_for(asyncio.shield(running),8)
  except BaseException as exc:
   failure=type(exc).__name__;progress('run_failed_'+failure)
   (out/(cls.__module__+'-'+str(resistant)+'-original-exception.txt')).write_text(traceback.format_exc(),encoding='utf-8')
  finally:
   elapsed=time.monotonic()-started;bridge=owned[0].resource.bridge if owned else None
   state=bridge.cleanup_state if bridge else 'NOT_STARTED';complete=bridge.cleanup_complete if bridge else False
   release_receiver.set()
   progress('fixture_cleanup')
   for writer in writers:writer.close()
   if writers:
    async with asyncio.timeout(1):await asyncio.gather(*(writer.wait_closed() for writer in writers),return_exceptions=True)
   if not running.done():running.cancel()
   await asyncio.gather(running,return_exceptions=True)
   pending=bridge.jobs.pending() if bridge else set()
   if pending:
    done,more=await asyncio.wait(pending,timeout=2)
    for task in more:task.cancel()
    await asyncio.gather(*more,return_exceptions=True)
   for task in manager.current_tasks():task.cancel()
   await asyncio.gather(*manager.current_tasks(),return_exceptions=True)
   server.close();await server.wait_closed()
   for task in handler_tasks:
    if not task.done():task.cancel()
   if handler_tasks:
    done,unfinished=await asyncio.wait(handler_tasks,timeout=1);assert not unfinished,'fixture_peer_handler_unsettled'
    await asyncio.gather(*done,return_exceptions=True)
   progress('fixture_settled')
  assert bridge is not None,'fixture_startup_failed_before_bridge_created'
  ack=[r for r in wire if r.get('event')=='me3_end_session_ack']
  assert len(ack)==1 and ack[0]['writes_fenced'] is True and bridge.peer_end_consumed
  return dict(failure=failure,cleanup_state=state,cleanup_complete=complete,elapsed_seconds=elapsed,peer_close_received=metadata.get('peer_close_received',False),receiver_done=bridge.session._client_task.done(),remaining_owned_jobs=len(bridge.jobs.pending()),remaining_manager_tasks=len(manager.current_tasks()),ack_count=len(ack))
 baseline=await one(old.DigitalTransport)
 assert baseline['failure'] is not None and baseline['cleanup_complete'] is False and baseline['cleanup_state']=='UNVERIFIED'
 fixed=await one(candidate.DigitalTransport)
 assert fixed['failure'] is None and fixed['cleanup_complete'] is True and fixed['cleanup_state']=='VERIFIED' and fixed['elapsed_seconds']<2 and fixed['receiver_done'] and fixed['remaining_owned_jobs']==fixed['remaining_manager_tasks']==0
 negative=await one(candidate.DigitalTransport,True)
 assert negative['failure'] is not None and negative['cleanup_complete'] is False and negative['cleanup_state']=='UNVERIFIED'
 assert all(r['remaining_owned_jobs']==r['remaining_manager_tasks']==0 for r in [baseline,fixed,negative])
 return dict(checks=dict(old_default10s_exceeds_same2s_guard=True,public1s_close_actual_session_settles=True,actual_upstream_launcher_and_cancel_used=True,exact_peer_ack_before_close=True,resistant_receiver_not_falsely_verified=True,fixture_owned_jobs_and_sockets_settled=True),details=dict(baseline=baseline,fixed=fixed,resistant=negative),tests=1)
