"""Real Pi device/MCP plumbing with scripted quiet tones or an injected fault. NO Google."""
import argparse,asyncio,json,logging,sys,time
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from loguru import logger
logger.remove();logging.disable(logging.CRITICAL)
import mcp_voice_agent as app
from speech_mcp.client import SpeechClients
from speech_mcp.runtime import SessionFailure
from scripts.m3_evidence import callback_continuity
from voice_agent import Settings,EventLog,configure_pi_audio
from pipecat.audio.vad.vad_analyzer import VADState
from pipecat.frames.frames import ErrorFrame

class RecordingLog(EventLog):
    def __init__(self,path):super().__init__(path);self.rows=[]
    def emit(self,event,**fields):super().emit(event,**fields);self.rows.append({'event':event,'monotonic_ns':time.monotonic_ns(),**fields})
class NoGoogle:
    async def answer(self,history):return 'Synthetic diagnostic response.'
    async def close(self):pass

async def exercise(scenario,events,settings):
    original_build=app.build_pipeline;built={}
    def build(*args):
        result=original_build(*args);runner,worker,transport,processor=result
        async def quiet(_):return VADState.QUIET
        processor.vad.analyze_audio=quiet # Deliberate fixture: no claim of speech/VAD recognition.
        built.update(runner=runner,processor=processor,transport=transport)
        return result
    app.build_pipeline=build;app.GoogleSpeech=NoGoogle
    app.SpeechClients=lambda events:SpeechClients(events,module='tests.mcp_tone_fixture_server')
    async def wait_for(predicate,seconds=6):
        async with asyncio.timeout(seconds):
            while not predicate():await asyncio.sleep(.01)
    def has(event,generation=None):return any(x['event']==event and (generation is None or x.get('generation')==generation) for x in events.rows)
    running=asyncio.create_task(app.run(settings,events))
    caught=None
    try:
        await wait_for(lambda:has('response_complete',0))
        if scenario=='fault':
            await built['processor'].push_frame(ErrorFrame(error='synthetic-private-frame-canary'))
        else:
            for old in (1,3):
                generation=await built['processor'].interrupt_response(source='scripted_fixture')
                if generation!=old:raise AssertionError('Unexpected diagnostic generation')
                built['processor'].controller.submit(bytes(6400),generation)
                await wait_for(lambda:has('generation_playout_started',old))
                await asyncio.sleep(.15)
                new=await built['processor'].interrupt_response(source='scripted_fixture')
                built['processor'].controller.submit(bytes(6400),new)
                await wait_for(lambda:has('response_complete',new))
            await built['runner'].cancel()
        try:await asyncio.wait_for(asyncio.shield(running),timeout=8)
        except SessionFailure as exc:caught=exc
    finally:
        if not running.done():
            if built:await built['runner'].cancel()
            await asyncio.gather(running,return_exceptions=True)
        app.build_pipeline=original_build
    if scenario=='fault':
        passed=(type(caught) is SessionFailure and has('pipeline_fault') and has('session_stop') and 'synthetic-private-frame-canary' not in events.path.read_text())
    else:
        passed=(caught is None and all(has('response_complete',g) for g in (2,4)) and all(not has('generation_playout_submitted',g) for g in (1,3)))
        clears=[x for x in events.rows if x['event']=='playout_clear']
        acknowledgements=[x for x in events.rows if x['event']=='callback_epoch_observed']
        captures=[x for x in events.rows if x['event']=='capture_callback']
        for source in [x for x in events.rows if x['event']=='scripted_interruption' and x['generation'] in (2,4)]:
            clear=next((x for x in clears if x['monotonic_ns']>=source['monotonic_ns']),None)
            if clear is None:passed=False;continue
            ack,capture=callback_continuity(clear,acknowledgements,captures)
            passed=passed and ack is not None and capture is not None
            events.emit('offline_callback_evidence',generation=source['generation'],clear_epoch=clear['epoch'],matching_epoch=ack is not None,capture_advanced=capture is not None,source='queue epoch/capture count; log timestamp is delayed; not acoustic')
    passed=passed and has('duplex_transport_closed') and has('session_end') and any(x['event']=='session_stop' and x.get('worker_finished') and x.get('cleanup_ok') for x in events.rows)
    events.emit('offline_scenario_result',scenario=scenario,passed=bool(passed),google_called=False,recognition='SYNTHETIC',vad='FORCED_QUIET_FIXTURE',audio='generated diagnostic tones',live_conversation='NOT_TESTED')
    return bool(passed)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--scenario',choices=['interruptions','fault'],default='interruptions');args=p.parse_args()
    settings=Settings(api_key='',max_seconds=20);configure_pi_audio(settings)
    events=RecordingLog(ROOT/'evidence/m3'/('offline-'+args.scenario+'-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+str(time.monotonic_ns())))
    events.emit('offline_fixture_scope',google_called=False,scripted_interruption=True,live_conversation='NOT_TESTED')
    try:
        passed=asyncio.run(exercise(args.scenario,events,settings))
        print(json.dumps({'scenario':args.scenario,'status':'PASS' if passed else 'FAIL','google_called':False,'live_conversation':'NOT_TESTED'}));return 0 if passed else 2
    except Exception as exc:
        print(json.dumps({'scenario':args.scenario,'status':'FAIL','error_type':type(exc).__name__}));return 2
    finally:events.close()
if __name__=='__main__':raise SystemExit(main())
