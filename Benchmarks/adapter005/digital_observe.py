"""Same-process digital/VAD observations; no physical callback or authority."""
import time
def attach(processor,bridge,events):
 if getattr(events,'processor',None) not in (None,processor):raise ValueError('one_digital_observation_owner_required')
 if processor.output_transport is not bridge.output():raise ValueError('actual_digital_output_binding_required')
 events.processor=processor
 original=processor.vad.analyze_audio;last=None
 async def observe(audio):
  nonlocal last
  result=await original(audio)
  if result!=last:
   events.emit('digital_vad_state',state=result.name,generation=processor.controller.clock.current,
    capture_count=processor.state.capture_count,software_ns=time.monotonic_ns(),
    observation_session=bridge.observation_session,physical_capture=False)
   last=result
  return result
 processor.vad.analyze_audio=observe
