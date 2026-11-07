"""Staged Pipecat1.12 digital transport over the course's existing session.

No connection is opened at import or construction. No audio device is opened.
The benchmark entry/resource authorization is a separate, still-blocked gate.
"""
import asyncio,json,time,uuid
from urllib.parse import urlsplit,parse_qs
from pipecat.frames.frames import InputAudioRawFrame,InterruptionFrame,LLMFullResponseEndFrame,LLMFullResponseStartFrame
from pipecat.processors.frame_processor import FrameDirection
from pipecat.transports.base_input import BaseInputTransport
from pipecat.transports.base_output import BaseOutputTransport
from pipecat.transports.base_transport import BaseTransport,TransportParams
from duplex_audio import DuplexState
from digital_core import DigitalCore,DigitalBridgeError
from owned_shutdown import OwnedJobs,observe,completed_ok,cancel_pending

def need(value,code):
 if not value:raise DigitalBridgeError(code)

def bounded_benchmark_websocket_close():
 """Public connect option only, in this dedicated external adapter process.

 A websocket's default10s close handshake exceeds our2s owned cleanup bound.
 One second allows protocol timeout/abort to settle before that same bound.
 It does not change HTTP, inference, trial windows, or normal production code.
 """
 import pipecat.transports.websocket.client as module
 original=module.websocket_connect
 if getattr(original,'_me3_benchmark_close_timeout',None)==1:return
 def connect(*args,**kwargs):
  need('close_timeout' not in kwargs or kwargs['close_timeout']==1,'benchmark_close_timeout_override_conflict')
  return original(*args,**{**kwargs,'close_timeout':1})
 connect._me3_benchmark_close_timeout=1
 module.websocket_connect=connect

class DigitalInput(BaseInputTransport):
 def __init__(self,owner,params):super().__init__(params);self.owner=owner;self.started=False
 async def setup(self,setup):
  await super().setup(setup);await self.owner.acquire('input',setup.task_manager)
 async def start(self,frame):
  await super().start(frame);await self.set_transport_ready(frame);self.started=True
 async def on_message(self,websocket,message):
  if isinstance(message,str):
   try:control=json.loads(message)
   except ValueError:return
   if type(control) is dict and control.get('event')=='me3_end_session':await self.owner.end_session(control)
   return
  if not isinstance(message,(bytes,bytearray)):return
  data=bytes(message)
  if not self.started or self.owner.closing:
   self.owner.core.dropped_before_ready_bytes+=len(data);return
  for pcm in self.owner.core.feed_input(data):
   await self.push_audio_frame(InputAudioRawFrame(audio=pcm,sample_rate=16000,num_channels=1))
 async def stop(self,frame):self.started=False;await super().stop(frame)
 async def cancel(self,frame):self.started=False;await super().cancel(frame)

class DigitalOutput(BaseOutputTransport):
 def __init__(self,owner,params):
  super().__init__(params);self.owner=owner;self.accepting=asyncio.Event();self.accepting.set()
 async def setup(self,setup):
  await super().setup(setup);await self.owner.acquire('output',setup.task_manager)
 async def start(self,frame):
  await super().start(frame);await self.set_transport_ready(frame)
  self.owner.state.loop=asyncio.get_running_loop();self.owner.watch(self.owner.core.start())
 async def write_audio_frame(self,frame):
  await self.accepting.wait()
  need(frame.sample_rate==self.owner.state.rate and frame.num_channels==self.owner.state.channels,'actual_native_final_format_required')
  epoch=self.owner.state.buffer.epoch
  while not self.owner.core.closed and not self.owner.core.failed:
   result=self.owner.core.offer_final_output(frame.audio,epoch)
   if result=='accepted':self.owner.state.writes+=1;return True
   if result=='stale':return False
   need(result=='full','unexpected_buffer_outcome');await asyncio.sleep(.005)
  return False
 async def process_frame(self,frame,direction):
  if isinstance(frame,LLMFullResponseStartFrame):
   generation=getattr(frame,'m3_generation',None)
   need(generation is None or type(generation) is int and generation>=0,'digital_generation_metadata_invalid')
   self.owner.epoch_generations[self.owner.state.buffer.epoch]=generation
   # Retain only bounded observational mappings; no ownership decision uses it.
   self.owner.epoch_generations=dict(list(self.owner.epoch_generations.items())[-32:])
  if isinstance(frame,InterruptionFrame):
   self.accepting.clear();self.owner.core.flow.clear()
   try:
    # The normal benchmark lane has no mixers or uninterruptible side effects.
    need(all(not s._audio_queue.has_uninterruptible and s._mixer is None for s in self._media_senders.values()),'uninterruptible_digital_media_not_qualified')
    await super().process_frame(frame,direction) # settle/reset actual sender first
    await self.owner.core.interrupt()          # epoch fence, settle wire, clear
   except BaseException:
    self.owner.core.failed=True;self.owner.core.ready=False;raise
   finally:self.accepting.set()
  else:await super().process_frame(frame,direction)
 async def write_transport_frame(self,frame):
  if isinstance(frame,LLMFullResponseEndFrame):
   # This hook runs inside the real media sender AFTER its preceding audio and
   # TTSStoppedFrame tail flush. A bare empty core buffer is insufficient.
   epoch=self.owner.state.buffer.epoch
   await self.owner.drain(2)
   if epoch!=self.owner.state.buffer.epoch:return
   g=getattr(frame,'m3_generation',None)
   self.owner.events.emit('digital_media_tail_drained',generation=g,epoch=epoch,physical_audio=False)
   if g==0:
    self.owner.greeting_tail.set();await self.owner.maybe_ready()

class DigitalTransport(BaseTransport):
 """One owned course session, real BaseInput/BaseOutput, existing PCM buffer."""
 def __init__(self,course_transport,settings,events,*,shutdown_seconds=2,ready_metadata=None):
  super().__init__();need(type(settings.output_rate) is int and settings.output_rate in {24000,48000} and type(settings.output_channels) is int and settings.output_channels in {1,2},'supported_native_final_format_required')
  need(getattr(course_transport,'_input',None) is None and getattr(course_transport,'_output',None) is None,'unstarted_course_transport_required')
  session=getattr(course_transport,'_session',None);uri=getattr(session,'_uri',None)
  need(isinstance(uri,str) and urlsplit(uri).scheme in {'ws','wss'} and parse_qs(urlsplit(uri).query).get('out_sr')==['48000'],'course_wire_mono48k_declaration_required')
  self.course=course_transport;self.session=session;self.events=events;self.state=DuplexState(settings,events)
  need(type(shutdown_seconds) in {int,float} and 0<shutdown_seconds<=2,'bounded_owned_shutdown_required')
  bounded_benchmark_websocket_close()
  self.shutdown_seconds=shutdown_seconds;self.jobs=OwnedJobs();self.closing=False;self.write_blocked=False;self.cleanup_state='OPEN';self.supervision_required=False
  self.acquired=set();self.closed=False;self.cleanup_complete=False;self.voice_listening=asyncio.Event();self.greeting_tail=asyncio.Event();self.ready=asyncio.Event();self.failure=asyncio.Event();self.last_send_end=0;self._close_task=None;self._close_deadline=None
  self._event_original=None;self._event_hook=None;self._ready_jobs=set();self._ready_lock=asyncio.Lock()
  need(ready_metadata is None or type(ready_metadata) is dict and set(ready_metadata)=={'source_revision','contract_sha256'} and all(isinstance(v,str) and len(v)==64 for v in ready_metadata.values()),'source_bound_ready_metadata_required')
  self.ready_metadata=ready_metadata
  self.peer_end=asyncio.Event();self.peer_end_consumed=False
  async def pcm(data):
   need(not self.write_blocked,'digital_wire_writes_inhibited')
   need(await self.session.send(data) is True,'digital_wire_send_failed')
   need(not self.write_blocked,'digital_wire_write_settled_after_shutdown_unverified')
   self.last_send_end=asyncio.get_running_loop().time()+len(data)/96000
  async def control(value):
   need(not self.write_blocked,'digital_wire_writes_inhibited')
   need(await self.session.send(json.dumps(value,separators=(',',':'))) is True,'digital_wire_clear_failed')
  self.core=DigitalCore(self.state,pcm,control)
  self.observation_session=uuid.uuid4().hex;self.epoch_generations={}
  self.core.describe_epoch=lambda epoch:dict(generation=self.epoch_generations.get(epoch),
   observation_session=self.observation_session,clock='monotonic_ns; same process only')
  params=TransportParams(audio_in_enabled=True,audio_out_enabled=True,audio_in_sample_rate=16000,audio_in_channels=1,
   audio_out_sample_rate=settings.output_rate,audio_out_channels=settings.output_channels,audio_out_10ms_chunks=2,audio_out_end_silence_secs=0,
   audio_out_auto_silence=False,audio_out_write_timeout_secs=2)
  self._input=DigitalInput(self,params);self._output=DigitalOutput(self,params)
  # The session's existing callback and launcher event handlers stay intact.
  course_transport._input=self._input;course_transport._output=self._output
 def input(self):return self._input
 def output(self):return self._output
 def factory(self,settings,events):
  need(events is self.events and settings.output_rate==self.state.rate and settings.output_channels==self.state.channels,'exact_digital_factory_context_required')
  return self,self.state
 async def acquire(self,side,task_manager):
  need(side not in self.acquired and not self.closed and not self.closing,'single_side_setup_required')
  await self.session.setup(task_manager);self.acquired.add(side)
  await self.session.connect();need(self.session.is_connected,'digital_connection_required')
 def watch(self,job):
  def done(task):
   self._ready_jobs.discard(task)
   if not task.cancelled() and task.exception() is not None:
    if self.closing and isinstance(task.exception(),DigitalBridgeError) and task.exception().args in {('digital_bridge_closed_or_failed',),('digital_wire_writes_inhibited',)}:
     self.events.emit('digital_job_settled_during_shutdown',error_type=type(task.exception()).__name__,cleanup_verified=False)
     return
    self.core.failed=True;self.core.ready=False;self.failure.set()
    self.events.emit('digital_transport_failed',error_type=type(task.exception()).__name__,automatic_retry=False)
  job.add_done_callback(done);return job
 def bind_voice_events(self):
  need(self._event_original is None,'single_voice_event_binding_required')
  original=self.events.emit;self._event_original=original
  def emit(event,**fields):
   original(event,**fields)
   if event=='onit_voice_listening':
    self.voice_listening.set();job=asyncio.create_task(self.maybe_ready());self._ready_jobs.add(job);self.watch(job)
  self._event_hook=emit;self.events.emit=emit
 async def maybe_ready(self):
  async with self._ready_lock:
   if self.ready.is_set() or not self.voice_listening.is_set() or not self.greeting_tail.is_set():return
   need(not self.closed and not self.closing and not self.core.failed,'digital_readiness_failed')
   await self.drain(2)
   need(not self.closed and not self.closing and not self.core.failed,'digital_readiness_failed')
   self.core.mark_listening_ready()
   if self.ready_metadata is not None:
    marker=dict(event='me3_ready',session=self.observation_session,generation=0,physical_audio=False,**self.ready_metadata)
    need(await self.session.send(json.dumps(marker,separators=(',',':'))) is True,'digital_readiness_announcement_failed')
   self.ready.set()
   self.events.emit('digital_assistant_ready',input_rate=16000,wire_output_rate=48000,wire_channels=1,
    greeting_media_and_core_drained=True,physical_audio=False,benchmark_runner_barrier_still_required=True)
 async def end_session(self,control):
  """Host-only post-trial reset control, not model/task/action authority."""
  try:
   expected=dict(event='me3_end_session',session=self.observation_session,**(self.ready_metadata or {}))
   need(self.ready_metadata is not None and control==expected and set(control)==set(expected),'exact_peer_end_binding_required')
   need(self.ready.is_set() and not self.peer_end_consumed and not self.closing and not self.failure.is_set() and not self.core.failed,'current_healthy_unused_peer_end_required')
   observer=getattr(self,'stage_observer',None)
   if observer is not None:await asyncio.wait_for(observer.flush(),self.shutdown_seconds)
   self.peer_end_consumed=True;self.inhibit()
   await self.core._settle(self.core.inflight)
   need(not self.failure.is_set(),'prior_transport_failure_cannot_be_reset')
   ack={**expected,'event':'me3_end_session_ack','writes_fenced':True}
   need(await self.session.send(json.dumps(ack,separators=(',',':'))) is True,'peer_end_ack_send_failed')
   self.peer_end.set()
   self.events.emit('digital_peer_end_accepted',observation_session=self.observation_session,writes_fenced=True,normal_cleanup_still_required=True)
  except BaseException:
   self.failure.set();self.inhibit();raise
 async def drain(self,timeout):
  await self.core.wait_core_drained(timeout)
  deadline=self.last_send_end
  await asyncio.sleep(max(0,deadline-asyncio.get_running_loop().time()))
 def inhibit(self):
  self.closing=True;self.write_blocked=True;self.ready.clear();self.core.ready=False
  self.core.failed=True;self.core.flow.clear()
 async def close(self,*,deadline=None):
  self.inhibit()
  if self._close_task is None:
   now=asyncio.get_running_loop().time();self._close_deadline=min(now+self.shutdown_seconds,deadline) if deadline is not None else now+self.shutdown_seconds
   self._close_task=self.jobs.start(self._close(self._close_deadline))
  await observe({self._close_task},self._close_deadline)
  if not completed_ok(self._close_task):
   self.cleanup_state='UNVERIFIED';self.supervision_required=True
   raise DigitalBridgeError('digital_transport_cleanup_unverified')
 async def _close(self,deadline):
  websocket=getattr(self.session,'_websocket',None)
  core=self.jobs.start(self.core.close());release={side:self.jobs.start(self.session.disconnect()) for side in tuple(self.acquired)}
  ready=set(self._ready_jobs);cancel_pending(ready)
  done,pending=await observe({core,*release.values(),*ready},deadline)
  cancel_pending(pending)
  for side,task in release.items():
   if completed_ok(task):self.acquired.remove(side)
  if self._event_original is not None and self.events.emit is self._event_hook:self.events.emit=self._event_original
  active=[t for t in (self.core.pump,self.core.inflight) if t is not None and not t.done()]
  receiver=getattr(self.session,'_client_task',None);receiver_settled=receiver is None or receiver.done()
  verified=asyncio.get_running_loop().time()<=deadline and not pending and completed_ok(core) and self.core.cleanup_complete and not self.acquired and not self.session.is_connected and not active and receiver_settled
  self.cleanup_complete=verified;self.closed=verified;self.cleanup_state='VERIFIED' if verified else 'UNVERIFIED';self.supervision_required=not verified
  self.events.emit('digital_transport_cleanup',cleanup_state=self.cleanup_state,owned_jobs_unsettled=len(pending)+len(active),physical_audio=False,owner_process_supervision_required=not verified,receiver_task_settled=receiver_settled,benchmark_public_close_timeout_seconds=1,websocket_close_code=getattr(websocket,'close_code',None))
  need(verified,'digital_transport_cleanup_unverified')
