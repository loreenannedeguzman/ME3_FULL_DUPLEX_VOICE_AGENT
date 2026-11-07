"""Safe terminal cues and error-frame metadata; never render frame/provider payloads."""
from voice_agent import EventLog

def status_message(event,fields):
    if event=='mcp_connected':
        role={'asr':'ASR','tts':'TTS'}.get(fields.get('role'))
        return role+' MCP server connected.' if role else None
    messages={
        'session_start':'Servers ready. Listen for the greeting, then speak.',
        'local_speech_start':'Speech detected; stopping the previous response.',
        'local_speech_stop':'Speech ended; processing your turn.',
        'reasoning_start':'Preparing a reply.',
        'generation_playout_started':'Playing the response. You can interrupt by speaking.',
        'generation_playout_submitted':'Response submitted to audio output; listening.',
        'empty_transcription':'No clear words recognized. Please try again.',
        'turn_error':'That request failed. Check the metadata log; do not repeatedly retry.',
        'pipeline_fault':'Audio pipeline error; stopping safely.',
        'session_provider_limited':'Provider rate/quota limit reached; stopping. Check the selected service before retrying.',
        'turn_budget_reached':'Session request budget reached; stop and review the test.',
    }
    if event=='mcp_request_start':
        return {'asr':'Transcribing your utterance.','tts':'Generating speech.'}.get(fields.get('role'))
    if event=='tts_rate_wait':
        value=fields.get('wait_seconds')
        if type(value) in (int,float) and 0<=value<=122:
            return f'TTS quota pacing: waiting about {value:.0f} seconds. Please wait without repeating.'
        return 'TTS quota pacing: waiting. Please wait without repeating.'
    if event=='session_stop':
        return {'completed':'Session stopped.','time_limit':'Session time limit reached; stopped.',
                'provider_quota':'Provider quota reached; session stopped.',
                'speech_service_error':'Speech service error; session stopped without retry.',
                'pipeline_fault':'Pipeline fault; session stopped.',
                'caller_cancelled':'Session stopped by request.',
                'runner_error':'Worker error; session stopped.',
                'runner_cancelled':'Worker cancelled; session stopped.'}.get(fields.get('reason'),'Session stopped; inspect metadata.')
    return messages.get(event)

class StatusEventLog(EventLog):
    def emit(self,event,**metadata):
        super().emit(event,**metadata)
        message=status_message(event,metadata)
        if message:print(message,flush=True)

def make_fault_observer(events,failed):
    from pipecat.observers.base_observer import BaseObserver
    from pipecat.frames.frames import ErrorFrame
    class FaultObserver(BaseObserver):
        def __init__(self):super().__init__(observe_every_push=False);self.seen=set()
        async def on_push_frame(self,data):
            frame=data.frame
            if isinstance(frame,ErrorFrame) and frame.id not in self.seen:
                self.seen.add(frame.id)
                events.emit('pipeline_fault',frame_type=type(frame).__name__,frame_id=frame.id)
                failed.set()
    return FaultObserver()
