"""Same M6 scripts/clock/playback observers; opt-in M7 ASR/controller only."""
import asyncio,json,logging,re,sys,time
from collections import deque
from pathlib import Path
from benchmark_m6.storage import ROOT
from me3_m7.integration import apply
from me3_m7.client import M7SpeechClients

def run_path(name):
    if not re.fullmatch(r'run-[0-9]{8}-[0-9]{3}',name):raise ValueError('invalid_run_id')
    return ROOT/'evidence/m7'/name

def configure():
    app=apply()
    import speech_mcp.client as client_module
    client_module.SpeechClients=M7SpeechClients
    import benchmark_m6.runner as runner
    original_events=runner.Events;original_attach=runner.attach
    class Events(original_events):
        def emit(self,event,**data):
            pending=self.pending_since;super().emit(event,**data)
            if event=='response_complete' and data.get('generation')!=self.current_generation:self.pending_since=pending
        def command(self,command):
            if command['action']=='cue' and self.processor is not None:
                task=self.processor.controller.task
                if task is not None and not task.done():raise ValueError('previous_response_not_finished; wait_before_cue')
            return super().command(command)
    def attach(processor,transport,events):
        original_attach(processor,transport,events);original=processor.vad.analyze_audio;window=deque(maxlen=15);active=False
        async def observe(pcm):
            nonlocal active
            arrival=time.monotonic_ns()
            if not active:window.append(arrival)
            value=await original(pcm)
            if not active and value.name=='SPEAKING':
                active=True;events.emit('m7_capture_segment_start',generation=processor.controller.clock.current+1,observed_ns=time.monotonic_ns(),capture_segment_start_ns=window[0],method='PCM frame arrival at VAD, first retained preroll frame; processing proxy, not device/physical onset',last_capture_callback_ns=transport.state.last_capture_ns)
            elif active and value.name=='QUIET':
                active=False;events.emit('m7_capture_segment_end',generation=processor.controller.clock.current,observed_ns=time.monotonic_ns(),capture_segment_end_ns=arrival,method='last PCM frame arrival at VAD; actual byte/duration in m7_asr_payload',last_capture_callback_ns=transport.state.last_capture_ns);window.clear()
            return value
        processor.vad.analyze_audio=observe;return processor
    runner.run_path=run_path;runner.Events=Events;runner.attach=attach
    return runner
if __name__=='__main__':
    from loguru import logger
    logger.remove();logging.disable(logging.CRITICAL)
    runner=configure()
    try:asyncio.run(runner.run(sys.argv[1],sys.argv[2]))
    except KeyboardInterrupt:raise SystemExit(130)
    except Exception as exc:print(json.dumps({'status':'FAIL','error_type':type(exc).__name__}),file=sys.stderr);raise SystemExit(2)
