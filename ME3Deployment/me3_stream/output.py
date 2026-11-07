"""Session ownership and final-output integration; no model/browser calls in audio callbacks."""
import asyncio
from .audio import PCMInput,MediaAudioError
from .browser import Broker

class SessionPCM(PCMInput):
 def __init__(self,playing):super().__init__(capacity=76800);self.playing=playing
 def mix(self,voice,user_speaking=False):
  # Continue draining capture while paused, without replaying its queued tail.
  return super().mix(voice,user_speaking or not self.playing())

class SessionBinding:
 def __init__(self,root,events,*,current,voice,broker_factory=Broker):
  self.events=events;self.current=current;self.voice=voice;self.reference_bound=False;self.echo=None;self.typed_output=None;self.closed=False
  self.broker=None;self.pcm=SessionPCM(lambda:bool(self.broker and self.broker.playing and not self.broker.fault.is_set()))
  self.broker=broker_factory(root,pcm=self.pcm,reference_ready=self.reference_ready,voice_active=lambda:self.voice,current=current,events=events,ensure_output=None if voice else self.ensure_typed_output)
 async def open(self):await self.broker.__aenter__()
 def reference_ready(self):
  if self.closed or not self.current():return False
  if self.voice:return bool(self.reference_bound and self.echo is not None and not self.echo.closed and not self.echo.faulted)
  output=self.typed_output
  return bool(output and output.resource is not None and not output.closed and not output.closing and not output.fault.is_set())
 def bind_reference(self,echo):
  if echo.closed or echo.faulted:raise MediaAudioError('final_AEC_reference_unavailable')
  self.echo=echo;self.reference_bound=True
 async def ensure_typed_output(self):
  if self.voice or self.typed_output is None:raise MediaAudioError('owned_typed_output_required')
  await self.typed_output.ensure_open()
 def attach_voice(self,result,settings,*,echo_available):
  if echo_available is not True or (settings.output_rate,settings.output_channels)!=(48000,2):raise MediaAudioError('SoundCloud_requires_final_AEC_reference')
  _,_,transport,processor=result;output=transport.output();original=output.callback;analyze=processor.vad.analyze_audio;user=False
  async def vad(pcm):
   nonlocal user
   state=await analyze(pcm);user=state.name!='QUIET';return state
  def mixed(*args):
   voice,flag=original(*args)
   try:return self.pcm.mix(voice,user_speaking=user),flag
   except Exception:
    def failed():self.broker.fault.set();processor.pipeline_failed.set()
    loop=transport.state.loop
    if loop and not loop.is_closed():loop.call_soon_threadsafe(failed)
    else:failed()
    transport.state.post('assistant_stream_mix_failed',session_stopping=True)
    return voice,flag
  processor.vad.analyze_audio=vad;output.callback=mixed
  for name in ('backchannel','semantic_cue'):
   cue=getattr(processor,name,None)
   if cue is None:continue
   async def guarded(*args,_cue=cue,**kwargs):
    if self.broker.playing:
     transport.state.post('assistant_music_listener_cue_suppressed',reason='external_music_active',microphone_gated=False);return
    return await _cue(*args,**kwargs)
   setattr(processor,name,guarded)
  if hasattr(processor,'controller') and hasattr(processor,'semantic_cue'):processor.controller.listener_cue=processor.semantic_cue
  play=getattr(processor,'play',None)
  if play:
   async def guarded_play(pcm,generation,kind='response',**kwargs):
    if kind in {'backchannel','planning_ack'} and self.broker.playing:return
    return await play(pcm,generation,kind,**kwargs)
   processor.play=guarded_play
  self.events.emit('assistant_stream_output_attached',order='local_music then browser_PCM then final_AEC',user_speech_ducks_music=True,microphone_gated=False,reference_bound=False)
 async def watch(self,stop):
  await self.broker.fault.wait()
  self.events.emit('onit_candidate_failed',reason='owned_stream_output',automatic_retry=False);stop.set()
 async def close(self,registry=None):
  if self.closed:return
  try:
   await self.broker.close()
   if self.broker.stopped_cleanly and registry is not None and hasattr(registry,'journal'):
    count=registry.journal.end_music_owner(registry.music_owner,actions=('propose_stream_control',))
    if count:self.events.emit('assistant_stream_session_retired',count=count,original_outcome_verified=False,write_retried=False)
   if not self.broker.stopped_cleanly:
    self.events.emit('assistant_stream_cleanup_unverified',resources=self.broker.cleanup_errors,original_outcome_verified=False)
    raise MediaAudioError('owned_stream_cleanup_unverified')
  finally:self.closed=True;self.reference_bound=False
