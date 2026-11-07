"""Additive observations only: no PCM modification, forced VAD, prompt or buffer change."""
import asyncio,time

def attach(processor,transport,events):
    original=processor.vad.analyze_audio;active=False;last_state=None
    async def analyze(data):
        nonlocal active,last_state
        value=await original(data);now=time.monotonic_ns();state=value.name
        if state!=last_state:
            events.emit('m6_vad_state',observed_ns=now,state=state,generation=processor.controller.clock.current)
        if not active and state=='SPEAKING':
            active=True;generation=processor.controller.clock.current+1
            events.bind_generation(generation)
            events.emit('m6_speech_onset_proxy',observed_ns=now,generation=generation,method='Silero SPEAKING decision; not acoustic')
        elif active and state=='QUIET':
            active=False;events.emit('m6_endpoint',observed_ns=now,generation=processor.controller.clock.current,method='Silero QUIET decision; not acoustic speech end')
        last_state=state
        return value
    processor.vad.analyze_audio=analyze
    interrupt=processor.controller.interrupt
    def cancel():
        events.emit('m6_cancellation_request',observed_ns=time.monotonic_ns(),generation=processor.controller.clock.current,replacement_generation=processor.controller.clock.current+1)
        return interrupt()
    processor.controller.interrupt=cancel
    output=transport.output();callback=output.callback;state=transport.state
    def observed_callback(in_data,frame_count,time_info,status):
        result=callback(in_data,frame_count,time_info,status)
        now=time.monotonic_ns();pcm=result[0]
        # Clock denotes return-ready, before posting telemetry; neither DAC nor acoustic.
        lead=time_info.get('output_buffer_dac_time',0)-time_info.get('current_time',0)
        state.post('m6_output_submission',observed_ns=now,epoch=state.last_callback_epoch,callback_count=state.callback_count,nonzero=bool(pcm and any(pcm)),block_seconds=frame_count/state.rate,reported_dac_lead_seconds=lead,capture_count=state.capture_count,status=status)
        return result
    output.callback=observed_callback
    return processor
