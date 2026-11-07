"""Offline-staged digital callback core; no sockets/devices/models/entry point.

Input:16kHz mono PCM16LE, losslessly repacketized to20ms. Output must already
be the unchanged native24/48kHz mono/stereo PCM; wire is48kHz mono.
Native smoothing/buffering precedes stateful ratecv conversion.24->48 starts
with one fewer wire sample, a fixed20.833us offset without cumulative drift.
The future Pipecat binding must retain its resampler, original controls and
normal startup/permission checks. This core alone is NOT an assistant adapter.
"""
import asyncio,audioop,struct,time
class DigitalBridgeError(ValueError):pass
def need(value,code):
 if not value:raise DigitalBridgeError(code)
def stereo_to_mono(pcm):
 need(type(pcm) is bytes and len(pcm)%4==0,'final_stereo_pcm16_alignment_required')
 out=bytearray(len(pcm)//2)
 for i,(left,right) in enumerate(struct.iter_unpack('<hh',pcm)):
  total=left+right;sample=total//2 if total>=0 else -((-total)//2)
  struct.pack_into('<h',out,i*2,sample)
 return bytes(out)
class DigitalCore:
 def __init__(self,state,send_pcm,send_control,*,shutdown_seconds=1):
  need(type(state.rate) is int and state.rate in {24000,48000} and type(state.channels) is int and state.channels in {1,2},'supported_native_PCM_format_required')
  self.frame_bytes=(state.rate//50)*state.channels*2;self.converter_state=None
  need(callable(send_pcm) and callable(send_control),'explicit_owned_wire_writers_required')
  need(type(shutdown_seconds) in {int,float} and 0<shutdown_seconds<=2,'bounded_shutdown_required')
  self.state=state;self.send_pcm=send_pcm;self.send_control=send_control;self.shutdown_seconds=shutdown_seconds
  self.pending=bytearray();self.ready=False;self.closed=False;self.failed=False;self.cleanup_complete=False;self.pump=None;self.inflight=None;self.serial=asyncio.Lock();self.flow=asyncio.Event();self.flow.set()
  self.input_frames=0;self.output_frames=0;self.dropped_before_ready_bytes=0;self.discarded_partial_input_bytes=0
  self.input_samples=0;self.output_samples=0;self.describe_epoch=lambda epoch:{}
 def mark_listening_ready(self):
  need(not self.closed and not self.failed,'digital_bridge_closed_or_failed');self.ready=True
 def feed_input(self,pcm):
  need(not self.closed and not self.failed,'digital_bridge_closed_or_failed');need(type(pcm) is bytes and len(pcm)<=32000,'bounded_binary_pcm_required')
  if not self.ready:
   self.dropped_before_ready_bytes+=len(pcm);return []
  self.pending.extend(pcm);result=[]
  while len(self.pending)>=640:
   frame=bytes(self.pending[:640]);del self.pending[:640];result.append(frame)
   self.input_frames+=1;self.state.capture_count+=1
   start=self.input_samples;self.input_samples+=320
   self.state.events.emit('digital_input_frame_admitted',frame_index=self.input_frames-1,
    sample_start=start,sample_end=self.input_samples,sample_rate=16000,channels=1,
    software_ns=time.monotonic_ns(),physical_capture=False)
  return result
 def offer_final_output(self,pcm,epoch):
  need(not self.closed and not self.failed,'digital_bridge_closed_or_failed');need(type(pcm) is bytes and len(pcm)%(self.state.channels*2)==0,'native_pcm16_alignment_required')
  return self.state.buffer.offer(pcm,epoch)
 async def step(self):
  need(not self.closed and not self.failed,'digital_bridge_closed_or_failed')
  await self.flow.wait()
  async with self.serial:
   if not self.flow.is_set():return
   need(not self.closed and not self.failed,'digital_bridge_closed_or_failed')
   need(self.inflight is None,'concurrent_digital_write_refused');self.inflight=asyncio.current_task()
   try:
    pcm,epoch,played,queued=self.state.buffer.take(self.frame_bytes)
    # take() is the same SuspendableBuffer path used by actual callback output.
    if epoch!=self.state.buffer.epoch:return
    mono=stereo_to_mono(pcm) if self.state.channels==2 else pcm
    if self.state.rate!=48000:mono,self.converter_state=audioop.ratecv(mono,2,1,self.state.rate,48000,self.converter_state)
    await self.send_pcm(mono)
    start=self.output_samples;self.output_samples+=len(mono)//2
    self.output_frames+=1;self.state.callback_count+=1;self.state.last_callback_epoch=epoch
    self.state.events.emit('digital_output_frame_submitted',epoch=epoch,played_bytes=played,queued_bytes=queued,
     frame_index=self.output_frames-1,sample_start=start,sample_end=self.output_samples,
     sample_rate=48000,channels=1,native_sample_rate=self.state.rate,native_channels=self.state.channels,**self.describe_epoch(epoch),
     software_ns=time.monotonic_ns(),physical_callback=False,AEC_reference=False,
     observation='in_memory_or_course_wire_send_completed; not audible onset')
   except asyncio.CancelledError:raise
   except Exception:
    self.failed=True;self.ready=False;raise
   finally:self.inflight=None
 async def _settle(self,task):
  if task is None or task is asyncio.current_task():return
  if not task.done():task.cancel()
  done,_=await asyncio.wait({task},timeout=self.shutdown_seconds)
  need(bool(done),'digital_send_cancellation_unsettled')
  await asyncio.gather(task,return_exceptions=True)
 async def interrupt(self):
  need(not self.closed and not self.failed,'digital_bridge_closed_or_failed')
  self.flow.clear();epoch,discarded=self.state.buffer.clear();self.state.interruptions+=1
  # Settle the only binary writer before the ordered wire clear; no stale
  # binary submission can follow a successfully completed clear.
  try:
   await self._settle(self.inflight)
   async with self.serial:
    self.converter_state=None # No prior epoch interpolation history survives clear.
    await asyncio.wait_for(self.send_control({'event':'clear'}),self.shutdown_seconds)
  except BaseException:
   self.failed=True;self.ready=False;raise
  finally:self.flow.set()
  self.state.events.emit('digital_output_clear',epoch=epoch,discarded_bytes=discarded,physical_callback=False)
  return epoch
 async def wait_core_drained(self,timeout):
  need(type(timeout) in {int,float} and 0<timeout<=2,'bounded_core_drain_timeout_required')
  deadline=asyncio.get_running_loop().time()+timeout
  while True:
   need(not self.closed and not self.failed,'digital_bridge_closed_or_failed')
   delegate=getattr(self.state.buffer,'delegate',self.state.buffer)
   with delegate._lock:queued=delegate._size
   if queued==0 and self.inflight is None:return
   if asyncio.get_running_loop().time()>=deadline:raise DigitalBridgeError('digital_core_drain_timeout')
   await asyncio.sleep(.001)
 def start(self):
  need(not self.closed and not self.failed and self.pump is None,'single_digital_pump_required')
  async def run():
   loop=asyncio.get_running_loop();deadline=loop.time()
   while not self.closed:
    job=asyncio.create_task(self.step())
    try:await job
    except asyncio.CancelledError:
     if self.closed or asyncio.current_task().cancelling():raise
    now=loop.time();deadline=max(deadline,now-.02)+.02
    await asyncio.sleep(max(0,deadline-loop.time()))
  self.pump=asyncio.create_task(run());return self.pump
 async def close(self):
  if self.closed:
   need(self.cleanup_complete,'digital_cleanup_incomplete');return
  self.closed=True;self.ready=False;self.flow.set()
  self.discarded_partial_input_bytes+=len(self.pending);self.pending.clear()
  self.state.buffer.clear();self.converter_state=None
  try:
   await self._settle(self.pump);await self._settle(self.inflight)
   if not self.failed:
    async with self.serial:await asyncio.wait_for(self.send_control({'event':'clear'}),self.shutdown_seconds)
  except BaseException:
   self.failed=True;self.ready=False;raise
  self.cleanup_complete=True
  self.state.events.emit('digital_bridge_closed',discarded_partial_input_bytes=self.discarded_partial_input_bytes,physical_audio_opened=False,remote_clear_verified=not self.failed)
