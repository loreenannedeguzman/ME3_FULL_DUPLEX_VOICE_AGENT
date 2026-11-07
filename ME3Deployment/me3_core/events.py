"""Lightweight functional evidence; no transcripts, PCM or calibrated acoustic claim."""
import time
from speech_mcp.diagnostics import StatusEventLog
class CoreEvents(StatusEventLog):
    def __init__(self,*args):super().__init__(*args);self.active=None;self.current_generation=0;self.generations={};self.processor=None;self.started=set();self.observations=[]
    def emit(self,event,**fields):
        g=fields.get('generation',fields.get('epoch'))
        if event in {'core_speech_onset','local_speech_start','core_turn_started'} and g is not None:
            self.current_generation=g;self.generations.setdefault(g,self.active)
        fields.setdefault('episode_id',self.generations.get(g,self.active))
        super().emit(event,**fields)
        if event=='core_turn_settled':print('Turn settled; microphone capture continues.',flush=True)

def attach(processor,transport,events):
    events.processor=processor;analyze=processor.vad.analyze_audio;speaking=False;last=None
    async def vad(pcm):
        nonlocal speaking,last
        state=await analyze(pcm)
        if state.name!=last:events.emit('core_vad_state',state=state.name,generation=processor.controller.clock.current);last=state.name
        if not speaking and state.name=='SPEAKING':
            speaking=True;events.emit('core_speech_onset',generation=processor.controller.clock.current+1,observed_ns=time.monotonic_ns(),method='VAD decision proxy; not physical speech onset')
        elif speaking and state.name=='QUIET':
            speaking=False;events.emit('core_endpoint',generation=processor.controller.clock.current,observed_ns=time.monotonic_ns(),method='VAD endpoint proxy')
        return state
    processor.vad.analyze_audio=vad
    output=transport.output();callback=output.callback;seen=set();epoch=None
    def observed(*args):
        nonlocal epoch
        result=callback(*args);now=time.monotonic_ns();state=transport.state;current=state.last_callback_epoch
        if epoch!=current:
            state.post('core_callback_epoch',epoch=current,previous_epoch=epoch,observed_ns=now,capture_count=state.capture_count,method='software callback epoch; not audible stop');epoch=current
        if current not in seen and result[0] and any(result[0]):
            seen.add(current);state.post('core_first_submission',epoch=current,observed_ns=now,capture_count=state.capture_count,method='first nonzero callback return-ready; not acoustic onset')
        return result
    output.callback=observed
