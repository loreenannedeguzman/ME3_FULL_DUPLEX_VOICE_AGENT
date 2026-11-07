"""Read-only VAD/callback observations; actual identity comes from the frozen controller."""
import time

def attach(processor,transport,events):
    original=processor.vad.analyze_audio;active=False;last=None
    async def vad(pcm):
        nonlocal active,last
        value=await original(pcm);now=time.monotonic_ns();state=value.name
        if state!=last:events.emit('bf_vad_state',state=state,observed_ns=now,trial_id=events.active)
        if not active and state=='SPEAKING':
            active=True
            events.emit('bf_speech_onset',observed_ns=now,token=processor.token+1,trial_id=events.active,output_kind=processor.output_kind,output_generation=processor.output_generation,method='Silero SPEAKING decision proxy, not acoustic onset')
        elif active and state=='QUIET':
            active=False;events.emit('bf_endpoint',observed_ns=now,token=processor.token,trial_id=events.active,method='Silero QUIET decision proxy; same0.7s boundary as M6, not acoustic last phoneme')
        last=state;return value
    processor.vad.analyze_audio=vad
    output=transport.output();callback=output.callback;state=transport.state
    def observed_callback(in_data,frame_count,time_info,status):
        result=callback(in_data,frame_count,time_info,status);now=time.monotonic_ns();epoch=state.last_callback_epoch
        g=processor.output_generation;kind=processor.output_kind
        valid=g is not None and g==processor.controller.clock.current and epoch==state.buffer.epoch
        state.post('bf_audio_submission',observed_ns=now,epoch=epoch,generation=g if valid else None,kind=kind if valid else 'unknown',nonzero=bool(result[0] and any(result[0])),callback_count=state.callback_count,capture_count=state.capture_count,block_seconds=frame_count/state.rate,reported_dac_lead_seconds=time_info.get('output_buffer_dac_time',0)-time_info.get('current_time',0),status=status,method='callback return-ready, not physical audible onset/stop')
        return result
    output.callback=observed_callback
    return processor
