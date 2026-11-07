"""One actual buffer/BaseOutput case with authored wire and no devices/providers."""
import asyncio,hashlib,importlib.util,time
from pathlib import Path
from types import SimpleNamespace as NS
P=Path(__file__).resolve().parent

async def exercise(out):
 from voice_agent import Settings
 from duplex_audio import PlayoutBuffer
 from me3_completion.playback import SuspendableBuffer
 from pipecat.transports.base_output import BaseOutputTransport
 from pipecat.frames.frames import OutputAudioRawFrame
 import digital_transport as candidate
 spec=importlib.util.spec_from_file_location('baseline004',P/'baseline004.py');old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
 class Events:
  def __init__(self):self.rows=[]
  def emit(self,event,**fields):self.rows.append(dict(event=event,**fields))
 class Wire:
  _uri='ws://127.0.0.1:18894/?out_sr=48000'
  def __init__(self):self.calls=0;self.block=False;self.entered=asyncio.Event()
  async def send(self,data):
   self.calls+=1;self.entered.set()
   if self.block:await asyncio.Event().wait()
   return True
 def make(module):
  wire=Wire();events=Events();course=NS(_session=wire,_input=None,_output=None)
  bridge=module.DigitalTransport(course,Settings(api_key='',output_rate=24000,output_channels=1),events)
  bridge.state.buffer=SuspendableBuffer(PlayoutBuffer(5760));bridge.core.mark_listening_ready()
  output=bridge.output();errors=[]
  async def error(*args,**kwargs):errors.append(dict(args=[str(x) for x in args],permanent=kwargs.get('force_treat_as_permanent')))
  output.push_error=error # Authored pipeline error observer; actual sender guard remains.
  sender=BaseOutputTransport.MediaSender(output,destination=None,sample_rate=24000,audio_chunk_size=960,params=output._params)
  frame=OutputAudioRawFrame(audio=b'\x01\x00'*480,sample_rate=24000,num_channels=1)
  return bridge,wire,events,sender,frame,errors
 async def settle(bridge,sender,jobs):
  for job in jobs:
   if not job.done():job.cancel()
  if jobs:await asyncio.gather(*jobs,return_exceptions=True)
  await bridge.core.close();sender._executor.shutdown(wait=True)
  assert not [j for j in jobs if not j.done()]
 async def paused(module,resume):
  b,w,e,s,f,errors=make(module);buffer=b.state.buffer;epoch=buffer.epoch
  assert buffer.offer(b'\x02\x00'*2880,epoch)=='accepted';buffer.suspend()
  pump=b.core.start();job=asyncio.create_task(s._internal_write_audio_frame(f));tail=asyncio.create_task(b.drain(2)) if resume else None;start=time.monotonic()
  try:
   # Intentional hold crosses the historical2s transport timer, with real
   # SuspendableBuffer silence and a live actual DigitalCore callback pump.
   await asyncio.sleep(2.15)
   held=dict(pending=not job.done(),wire_calls=w.calls,queued=buffer.delegate._size,epoch=buffer.epoch,played=sum(x.get('played_bytes',0) for x in e.rows))
   if resume:
    assert held['pending'] and not tail.done() and held['wire_calls']>=75 and held['queued']==5760 and held['epoch']==epoch and held['played']==0
    buffer.resume();assert await asyncio.wait_for(job,.5) is True
    await asyncio.wait_for(tail,.5)
    assert not errors and not b.core.failed and buffer.epoch==epoch
   else:assert job.done() and await job is False and len(errors)==1 and errors[0]['permanent'] is True
   return dict(elapsed=time.monotonic()-start,held=held,errors=len(errors))
  finally:await settle(b,s,[pump,job]+([tail] if tail is not None else []))
 baseline=await paused(old,False);fixed=await paused(candidate,True)
 b,w,e,s,f,errors=make(candidate);buffer=b.state.buffer;epoch=buffer.epoch;buffer.offer(b'\x02\x00'*2880,epoch);buffer.suspend()
 job=asyncio.create_task(s._internal_write_audio_frame(f))
 try:
  await asyncio.sleep(.02);job.cancel();r=await asyncio.gather(job,return_exceptions=True)
  assert isinstance(r[0],asyncio.CancelledError) and buffer.delegate._size==5760 and buffer.epoch==epoch and not errors
 finally:await settle(b,s,[job])
 b,w,e,s,f,errors=make(candidate);buffer=b.state.buffer;epoch=buffer.epoch;buffer.offer(b'\x02\x00'*2880,epoch);buffer.suspend()
 job=asyncio.create_task(s._internal_write_audio_frame(f))
 try:
  await asyncio.sleep(.02);buffer.clear();assert await asyncio.wait_for(job,.2) is False
  assert buffer.epoch==epoch+1 and buffer.delegate._size==0 and not errors
 finally:await settle(b,s,[job])
 b,w,e,s,f,errors=make(candidate);buffer=b.state.buffer;buffer.offer(b'\x02\x00'*2880,buffer.epoch);start=time.monotonic()
 try:
  assert await s._internal_write_audio_frame(f) is False
  active_elapsed=time.monotonic()-start;assert 1.9<=active_elapsed<2.6 and len(errors)==1 and b.core.failed and b.failure.is_set()
  assert any(x['event']=='digital_active_buffer_stall' for x in e.rows)
 finally:await settle(b,s,[])
 b,w,e,s,f,errors=make(candidate);w.block=True;start=time.monotonic()
 try:
  try:await b.core.step();raise AssertionError('wire stall accepted')
  except TimeoutError:pass
  wire_elapsed=time.monotonic()-start;assert 1.9<=wire_elapsed<2.6 and w.calls==1 and b.core.failed
 finally:await settle(b,s,[])
 return dict(checks=dict(historical_blanket2s_false_failure_reproduced=True,actual_suspension_over2s_preserves_PCM_then_resumes=True,paused_tail_drain_uses_same_active_budget=True,silence_not_counted_as_queued_speech_played=True,cancellation_settles_preserving_epoch=True,new_epoch_rejects_old_pending_frame=True,active_full_stall_retains2s_bound=True,actual_wire_stall_retains2s_bound=True,owned_tasks_settled=True),details=dict(baseline=baseline,fixed=fixed,active_stall_seconds=active_elapsed,wire_stall_seconds=wire_elapsed),tests=1)
