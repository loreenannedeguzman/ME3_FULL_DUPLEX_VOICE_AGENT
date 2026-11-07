"""Explicit one-utterance real TTS MCP probe; optional Pi playback, never ASR/text calls."""
import argparse,asyncio,json,logging,os,sys,time
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
from speech_mcp.tts_config import TTSConfig
from speech_mcp.client import SpeechClients
from speech_mcp.runtime import supervise
from voice_agent import Settings,EventLog,configure_pi_audio

TEXT='This is the local speech test. Please confirm that you can hear me clearly.'

class RecordingLog(EventLog):
    def __init__(self,path):super().__init__(path);self.rows=[]
    def emit(self,event,**fields):super().emit(event,**fields);self.rows.append({'event':event,**fields})

async def probe(events,play):
    async with SpeechClients(events) as clients:
        if not play:
            data=await clients.call('tts',0,text=TEXT,style='normal')
            events.emit('backend_probe_generated',byte_count=data['byte_count'],sample_rate=data['sample_rate'],channels=data['channels'],provider_api=data['provider_api'])
            return
        import mcp_voice_agent as app
        from pipecat.audio.vad.vad_analyzer import VADState
        settings=Settings(api_key='',max_seconds=55);configure_pi_audio(settings)
        runner,worker,transport,processor=app.build_pipeline(settings,events,clients,None)
        async def quiet(_):return VADState.QUIET
        processor.vad.analyze_audio=quiet # Diagnostic only: no recognition or microphone transmission.
        processor.controller.greet=lambda:processor.controller.spawn(processor.controller.speak(TEXT,0,'normal',greeting=True))
        await runner.add_workers(worker)
        async def finish():
            async with asyncio.timeout(50):
                while not any(r['event']=='response_complete' for r in events.rows):
                    if clients.failure.is_set() or clients.limit_reached.is_set():return
                    await asyncio.sleep(.05)
                await asyncio.sleep(.35) # Drain bounded output queue; audibility still requires operator.
                await runner.cancel()
        ending=asyncio.create_task(finish())
        try:
            reason=await supervise(runner,processor.controller,transport,events,clients.limit_reached,processor.pipeline_failed,55,service_failed=clients.failure)
            if reason!='completed':raise RuntimeError('backend_playback_probe_incomplete')
        finally:ending.cancel();await asyncio.gather(ending,return_exceptions=True)
        if not any(r['event']=='response_complete' for r in events.rows):raise RuntimeError('backend_probe_missing_response')
        events.emit('backend_probe_playback_submitted',audibility='OPERATOR_CONFIRMATION_REQUIRED',live_conversation='NOT_TESTED')

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--synthesize',action='store_true');parser.add_argument('--play',action='store_true');args=parser.parse_args()
    load_dotenv(ROOT/'.env',override=False,encoding='utf-8-sig')
    from loguru import logger
    logger.remove();logging.disable(logging.CRITICAL)
    events=None
    try:
        config=TTSConfig.from_env()
        if not args.synthesize and not args.play:
            print(json.dumps({**config.public_info(),'synthesis':'NOT_REQUESTED'}));return 0
        config.require_ready()
        events=RecordingLog(ROOT/'evidence/m3'/('tts-backend-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+str(time.monotonic_ns())))
        events.emit('backend_probe_scope',**config.public_info(),tts_request_budget=1,asr_requests=0,reasoning_requests=0,forced_quiet_vad=bool(args.play),live_conversation='NOT_TESTED')
        asyncio.run(probe(events,args.play))
        print(json.dumps({'status':'PASS','provider':config.provider,'tts_requests':1,'asr_requests':0,'reasoning_requests':0,'live_conversation':'NOT_TESTED','audibility':'OPERATOR_CONFIRMATION_REQUIRED' if args.play else 'NOT_TESTED'}));return 0
    except Exception as exc:
        print(json.dumps({'status':'FAIL','error_type':type(exc).__name__}));return 2
    finally:
        if events:events.close()
if __name__=='__main__':raise SystemExit(main())
