"""M3: Pi mic/VAD -> real ASR MCP -> text agent -> real TTS MCP -> interruptible output."""
import argparse,asyncio,json,logging,os,platform,sys,time
from collections import deque
from datetime import datetime,timezone
from pathlib import Path
from dotenv import load_dotenv
from voice_agent import ROOT,Settings,EventLog,configure_pi_audio,output_channel_adapter
from speech_mcp.client import SpeechClients
from speech_mcp.tts_config import TTSConfig
from speech_mcp.controller import TurnController
from speech_mcp.provider import GoogleSpeech,ASR_MODEL,TTS_MODEL,TEXT_MODEL
from speech_mcp.contracts import MAX_UTTERANCE_SECONDS,human_turn_limit
from speech_mcp.runtime import supervise,SessionFailure
from speech_mcp.diagnostics import StatusEventLog,make_fault_observer

def build_pipeline(settings,events,clients,provider):
    from duplex_audio import make_duplex_transport
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from pipecat.audio.vad.vad_analyzer import VADParams,VADState
    from pipecat.frames.frames import InputAudioRawFrame,OutputAudioRawFrame,InterruptionFrame,StartFrame,CancelFrame,EndFrame,TTSStartedFrame,TTSStoppedFrame,LLMFullResponseStartFrame,LLMFullResponseEndFrame
    from pipecat.processors.frame_processor import FrameProcessor,FrameDirection
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.worker import PipelineParams,PipelineWorker
    from pipecat.workers.runner import WorkerRunner
    transport,state=make_duplex_transport(settings,events)
    class VoiceProcessor(FrameProcessor):
        def __init__(self):
            super().__init__()
            self.vad=SileroVADAnalyzer(params=VADParams(confidence=.7,start_secs=.15,stop_secs=.7,min_volume=.6))
            self.vad.set_sample_rate(16000)
            self.preroll=deque(maxlen=15);self.speaking=False;self.pcm=bytearray();self.overflow=False
            self.controller=TurnController(clients,provider,events,self.play,max_turns=human_turn_limit(os.getenv('M3_MAX_HUMAN_TURNS','8')))
            self.pipeline_failed=asyncio.Event()
        async def play(self,pcm,generation):
            async def emit(frame):
                frame.m3_generation=generation
                await self.push_frame(frame)
            await emit(LLMFullResponseStartFrame());await emit(TTSStartedFrame())
            first=True
            for offset in range(0,len(pcm),960):
                if not self.controller.current(generation):return
                await emit(OutputAudioRawFrame(audio=pcm[offset:offset+960],sample_rate=24000,num_channels=1))
                events.output_frames+=1
                if first:
                    events.emit('generation_playout_started',generation=generation,capture_count=state.capture_count)
                    first=False
                await asyncio.sleep(.02)
            if self.controller.current(generation):
                await emit(TTSStoppedFrame());await emit(LLMFullResponseEndFrame())
                events.emit('generation_playout_submitted',generation=generation,capture_count=state.capture_count)
        async def interrupt_response(self,source='vad'):
            if source not in {'vad','scripted_fixture'}:raise ValueError('invalid_interruption_source')
            generation=self.controller.interrupt()
            events.emit('local_speech_start' if source=='vad' else 'scripted_interruption',generation=generation,capture_count=state.capture_count)
            await self.push_frame(InterruptionFrame())
            return generation
        async def process_frame(self,frame,direction):
            await super().process_frame(frame,direction)
            if direction==FrameDirection.UPSTREAM:
                await self.push_frame(frame,direction);return
            if isinstance(frame,StartFrame):
                await self.push_frame(frame,direction)
                self.controller.greet();return
            if isinstance(frame,(CancelFrame,EndFrame)):
                await self.controller.close();await self.push_frame(frame,direction);return
            if not isinstance(frame,InputAudioRawFrame):
                await self.push_frame(frame,direction);return
            events.input_frames+=1
            if frame.sample_rate!=16000 or frame.num_channels!=1:raise ValueError('unexpected_capture_format')
            was_speaking=self.speaking
            if not was_speaking:self.preroll.append(frame.audio)
            activity=await self.vad.analyze_audio(frame.audio)
            if not was_speaking and activity==VADState.SPEAKING:
                self.speaking=True;self.overflow=False;self.pcm=bytearray(b''.join(self.preroll));self.preroll.clear()
                await self.interrupt_response()
            elif was_speaking:
                if not self.overflow:self.pcm.extend(frame.audio)
                if len(self.pcm)>16000*2*MAX_UTTERANCE_SECONDS:
                    self.pcm.clear();self.overflow=True
                    events.emit('utterance_limit_reached',generation=self.controller.clock.current)
                if activity==VADState.QUIET:
                    self.speaking=False
                    events.emit('local_speech_stop',generation=self.controller.clock.current,capture_count=state.capture_count)
                    if not self.overflow and len(self.pcm)>=3200:
                        events.emit('utterance_committed',generation=self.controller.clock.current,byte_count=len(self.pcm))
                        self.controller.submit(bytes(self.pcm),self.controller.clock.current)
                    self.pcm.clear();self.preroll.clear()
        async def cleanup(self):
            await self.controller.close()
            self.vad._executor.shutdown(wait=False,cancel_futures=True)
            await super().cleanup()
    processor=VoiceProcessor()
    class GenerationFilter(FrameProcessor):
        async def process_frame(self,frame,direction):
            await super().process_frame(frame,direction)
            generation=getattr(frame,'m3_generation',None)
            if generation is not None and not processor.controller.current(generation):return
            await self.push_frame(frame,direction)
    pipeline=Pipeline([transport.input(),processor,output_channel_adapter(settings.output_channels),GenerationFilter(),transport.output()])
    worker=PipelineWorker(pipeline,params=PipelineParams(audio_in_sample_rate=16000,audio_out_sample_rate=settings.output_rate),idle_timeout_secs=settings.max_seconds,observers=[make_fault_observer(events,processor.pipeline_failed)])
    tts_config=TTSConfig.from_env()
    events.emit('m3_configuration',tts_provider=tts_config.provider,asr_model=os.getenv('ASR_MODEL',ASR_MODEL),tts_model=tts_config.model,text_model=os.getenv('TEXT_MODEL',TEXT_MODEL),vad='Silero ONNX CPU',vad_start_secs=.15,vad_stop_secs=.7,preroll_ms=300,utterance_max_seconds=MAX_UTTERANCE_SECONDS,output_buffer_ms=120,max_human_turns=processor.controller.max_turns,continuous_audio_mcp=False)
    return WorkerRunner(),worker,transport,processor

async def run(settings,events,build_only=False):
    provider=GoogleSpeech() if not build_only else None
    deferred=None
    try:
        if build_only:
            _,_,transport,processor=build_pipeline(settings,events,None,None)
            await processor.cleanup();await transport.close()
            events.emit('offline_pipeline_built',provider_connected=False)
            return
        async with SpeechClients(events) as clients:
            runner,worker,transport,processor=build_pipeline(settings,events,clients,provider)
            await runner.add_workers(worker)
            events.emit('session_start',host=platform.system(),machine=platform.machine(),max_seconds=settings.max_seconds,speech_path='asr_mcp-text_agent-tts_mcp')
            reason='startup'
            try:
                reason=await supervise(runner,processor.controller,transport,events,clients.limit_reached,processor.pipeline_failed,settings.max_seconds,service_failed=getattr(clients,'failure',None))
            except BaseException as exc:
                deferred=exc
                reason='caller_cancelled' if isinstance(exc,asyncio.CancelledError) else 'worker_or_cleanup_error'
            finally:
                events.emit('session_end',input_frames=events.input_frames,output_frames=events.output_frames,human_turns=processor.controller.turns,error_events=events.counts.get('turn_error',0),pipeline_faults=events.counts.get('pipeline_fault',0),stop_reason=reason)
            if deferred is None and (reason in {'provider_quota','pipeline_fault','runner_cancelled','speech_service_error'} or events.error_seen or not events.output_frames or events.counts.get('turn_error',0)):
                deferred=SessionFailure('live_run_incomplete_or_error')
        # Raise outside SDK context managers so expected failure isn't wrapped by their task groups.
        if deferred is not None:raise deferred
    finally:
        if provider:await provider.close()

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--build-check',action='store_true');args=p.parse_args()
    load_dotenv(ROOT/'.env',encoding='utf-8-sig')
    from loguru import logger
    logger.remove();logging.disable(logging.CRITICAL)
    events=None
    try:
        settings=Settings.load(live=not args.build_check)
        configure_pi_audio(settings)
        events=StatusEventLog(ROOT/'evidence/m3'/('runtime-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+str(time.monotonic_ns())))
        asyncio.run(run(settings,events,args.build_check));return 0
    except KeyboardInterrupt:return 130
    except Exception as exc:
        print(json.dumps({'status':'FAIL','error_type':type(exc).__name__}),file=sys.stderr);return 2
    finally:
        if events:events.close()
if __name__=='__main__':raise SystemExit(main())
