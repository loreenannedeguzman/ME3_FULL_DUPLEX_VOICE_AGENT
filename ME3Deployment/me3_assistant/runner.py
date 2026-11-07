"""Standalone normal assistant; no experiment console or stdin command dependency."""
import argparse,asyncio,json,logging,os,sys,uuid
from dataclasses import replace
from datetime import datetime,timezone
from pathlib import Path
from dotenv import load_dotenv
from voice_agent import ROOT,Settings,configure_pi_audio
from me3_core.events import CoreEvents,attach as attach_observers
from me3_agent.integration import apply
from me3_agent.speech import AgentSpeechClients
from speech_mcp.runtime import supervise
from me3_assistant.config import load,DEFAULT
from me3_assistant.guard import authorize,verify_sources,SharedBudget,NORMAL,lock
from me3_assistant.registry import Registry
from me3_assistant.router import Router
from me3_assistant.reasoning import Reasoning
from me3_assistant.actions import Journal
from me3_assistant.speech import AssistantController
from me3_assistant.voice_policy import install_normal_startup,reserve_open_session,bind_voice_session_stop
from speech_mcp.client import ProviderLimited

def bind_capacity_wait(model,controller,events):
    if hasattr(model,'transport'):
        model.transport.before_capacity_wait=lambda seconds:controller.credit_provider_wait(events.current_generation,seconds)

def runtime_profile(backend):
    from me3_assistant.voice_profile import DECISION_MODEL,PIPER_VOICE
    if backend not in {'groq','google'}:raise ValueError('unsupported_assistant_backend')
    return dict(tts_cpu_profile='duplex_2t_no_spin',backend=backend,asr_model='whisper-large-v3-turbo' if backend=='groq' else 'existing_google',reasoning_model=DECISION_MODEL if backend=='groq' else 'existing_google',tts_provider='piper',tts_model='piper:'+PIPER_VOICE,max_input_seconds=120 if backend=='groq' else 15,max_transcript_chars=4000 if backend=='groq' else 1000)

class Events(CoreEvents):
    def __init__(self,*args,observer=None,backend='google'):self.backend=backend;super().__init__(*args);self.bound_reached=asyncio.Event();self.observer=observer
    def emit(self,event,**fields):
        if event=='agent_overlap_deadline':fields['provider']=self.backend+' ASR MCP'
        if event=='turn_budget_reached':self.bound_reached.set()
        super().emit(event,**fields)
        if self.observer:self.observer(event,**fields)

def observe_pipeline(result,settings,events):
    _,_,transport,processor=result
    attach_observers(processor,transport,events)
    events.emit('assistant_audio_configuration',input_device_index=settings.input_device,output_device_index=settings.output_device,output_rate=settings.output_rate,output_channels=settings.output_channels,callback_measurement='software nonzero output proxy; not acoustic onset')

def normal_settings(config,backend):
    from me3_assistant.backends import audio_settings
    # The shared legacy loader retains its600second contract. Normal-mode
    # settings use the separately validated operating configuration.
    os.environ['MAX_SESSION_SECONDS']=str(min(config['runtime']['max_session_seconds'],600))
    return replace(audio_settings(backend),max_seconds=config['runtime']['max_session_seconds'])

async def run(config,observer=None,on_controller=None,backend=None,initial_history=None,stop_requested=None):
    # Public API rechecks the actual files; direct import cannot skip the CLI gate.
    config=load(Path(config['path']));source=verify_sources();grant,limits=authorize(config,source,backend=backend)
    from me3_assistant.backends import selected,check_ready,speech_module,reasoning_class,audio_settings
    backend=selected(grant,backend);check_ready(backend);profile=runtime_profile(backend)
    if stop_requested is None:stop_requested=asyncio.Event()
    if sys.platform!='linux':raise ValueError('normal_voice_requires_Pi_Linux')
    budget=SharedBudget(grant,limits)
    # A session lease covers the action journal and prevents parallel normal sessions.
    with lock(NORMAL/'session.lock'):
        name=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]
        events=Events(NORMAL/'sessions'/name,observer=observer,backend=backend);ordinary=None;music_bus=None;echo=None
        try:
            if backend=='google':load_dotenv(ROOT/'.env',encoding='utf-8-sig')
            from me3_assistant.voice_profile import normal_piper_environment
            piper_environment=await asyncio.to_thread(normal_piper_environment,ROOT)
            os.environ.update(**piper_environment,MAX_SESSION_SECONDS=str(min(config['runtime']['max_session_seconds'],600)),M3_MAX_HUMAN_TURNS='8',PIPECAT_SMART_TURN_LOG_DATA='false')
            settings=normal_settings(config,backend);configure_pi_audio(settings)
            from me3_assistant.acoustic_echo import audio_mode,speaker_echo
            if audio_mode(grant)=='speaker_aec':echo=speaker_echo() # Native availability before model/MCP/audio startup.
            app=apply(events)
            import me3_completion.pipeline as normal_pipeline
            normal_pipeline.ConversationController=AssistantController
            class Speech(AgentSpeechClients):
                max_utterance_seconds=profile['max_input_seconds']
                max_transcript_chars=profile['max_transcript_chars']
                def __init__(self,*args,**kwargs):
                    super().__init__(*args,module=speech_module(backend),**kwargs)
                def require_tts_capacity(self,count):
                    if budget.remaining('tts')<count:
                        budget.exhausted.set();raise ProviderLimited('insufficient_complete_response_allowance')
                async def call(self,role,*args,**kwargs):
                    events.emit('assistant_budget_reserved',role=role,ordinal=reserve_open_session(budget,role,stop_requested));return await super().call(role,*args,**kwargs)
            class Model(reasoning_class(backend)):
                async def decide(self,*args):
                    events.emit('assistant_budget_reserved',role='reasoning',ordinal=reserve_open_session(budget,'reasoning',stop_requested));return await super().decide(*args)
            ordinary=Model(events)
            if hasattr(ordinary,"transport"):ordinary.transport.stop_requested=stop_requested
            if grant.get('local_action_limits',{}).get('music_control',0) or grant.get('profile')=='standing' and grant.get('routine_local_music') is True:
                from me3_assistant.music_audio import MusicBus
                music_bus=MusicBus(events.path.parent/'music-bus.pcm',create=True)
            async with Registry(events,config,**({'music_bus':music_bus.path} if music_bus else {})) as registry:
                registry.budget=budget;registry.journal=Journal(ROOT/'data/assistant-actions.jsonl')
                registry.allowed_actions=grant['allowed_actions'];registry.session_grant=grant;registry.local_action_directory=NORMAL/'local-action-budgets'
                reasoner=Router(ordinary,registry,events)
                async with Speech(events) as speech:
                    if backend=='groq':
                        from me3_assistant.worker_readiness import prepare_speech_worker
                        await prepare_speech_worker(speech)
                    result=app.m3.build_pipeline(settings,events,speech,reasoner)
                    result[3].state.buffer.enable_smoothing(settings.output_rate,settings.output_channels)
                    events.emit('assistant_playback_transition_policy',fade_milliseconds=5,suspension_preserves_queued_PCM=True,cancellation_discards_tail=True,AEC_reference='actual final callback PCM')
                    from me3_assistant.overlap import attach as attach_acknowledgment_surface
                    attach_acknowledgment_surface(result[3],speech,reasoner,events)
                    observe_pipeline(result,settings,events)
                    if music_bus:
                        from me3_assistant.music_audio import attach_music
                        attach_music(result,settings,music_bus,events)
                    if echo:
                        # Only normal speaker mode changes host output granularity.
                        # Capture/VAD/native AEC stay20ms; callback DAC timestamps
                        # align the split final60ms block to each capture frame.
                        result[2].state.configure_output_callback(3)
                        echo.output_callback_periods=3
                        events.emit('assistant_output_headroom_policy',output_callback_ms=60,capture_frame_ms=20,AEC_reference_frame_ms=20,maximum_software_queue_ms=120,active_discontinuity_fatal=True,acoustic_qualification=False)
                        from me3_assistant.acoustic_echo import attach as attach_echo
                        attach_echo(result,settings,echo,events,threaded_capture=True) # After final music mix; before streams open.
                    runner,worker,transport,processor=result
                    processor.max_pcm_bytes=profile['max_input_seconds']*32000
                    events.emit('assistant_capture_policy',maximum_seconds=processor.max_pcm_bytes/32000,endpoint_uses_final_seconds=15,full_audio_sent_to_asr=True,streaming_transcription=False)
                    processor.controller.max_pcm_bytes=processor.max_pcm_bytes
                    processor.controller.max_transcript_chars=profile['max_transcript_chars']
                    reasoner.controller=processor.controller;processor.controller.max_turns=config['runtime']['max_turns']
                    bind_capacity_wait(ordinary,processor.controller,events)
                    bind_voice_session_stop(reasoner,stop_requested,events)
                    if initial_history:processor.controller.history=list(initial_history)[-16:]
                    if on_controller:on_controller(processor.controller)
                    processor.controller.greet=lambda:processor.controller.spawn(processor.controller.speak("What's on your mind today?",0,'normal',greeting=True))
                    install_normal_startup(processor,events)
                    await runner.add_workers(worker)
                    events.emit('assistant_session_start',source_revision=source['revision'],config_sha256=config['sha256'],max_seconds=settings.max_seconds,max_turns=processor.controller.max_turns,normal_mode=True,operator_console_required=False,**profile)
                    quota=asyncio.Event()
                    async def watch_quota():
                        tasks=[asyncio.create_task(speech.limit_reached.wait()),asyncio.create_task(budget.exhausted.wait()),asyncio.create_task(events.bound_reached.wait())]
                        try:await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED);quota.set()
                        finally:
                            for task in tasks:task.cancel()
                            await asyncio.gather(*tasks,return_exceptions=True)
                    watcher=asyncio.create_task(watch_quota())
                    reason='startup'
                    try:
                        reason=await supervise(runner,processor.controller,transport,events,quota,processor.pipeline_failed,settings.max_seconds,service_failed=speech.failure,stop_requested=stop_requested)
                        if reason=='provider_quota' and events.bound_reached.is_set():reason='configured_turn_limit'
                        elif reason=='provider_quota' and budget.exhausted.is_set():reason='local_authorized_budget_limit'
                    except asyncio.CancelledError:reason='caller_cancelled';raise
                    except Exception:reason='runtime_or_cleanup_error';raise
                    finally:
                        watcher.cancel();await asyncio.gather(watcher,return_exceptions=True)
                        events.emit('assistant_session_end',reason=reason,tool_calls=registry.calls,unverified_actions=len(registry.journal.unresolved()))
                    if reason in {'pipeline_fault','runner_error','speech_service_error','runner_cancelled'}:raise RuntimeError('normal_session_failed')
        finally:
            try:
                if echo:
                    if hasattr(echo,'aclose'):await echo.aclose()
                    else:echo.close()
            finally:
                try:
                    if ordinary is not None:await ordinary.close()
                finally:
                    try:
                        if music_bus:music_bus.close(destroy=True)
                    finally:events.close()

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',type=Path,default=DEFAULT);p.add_argument('--check',action='store_true');args=p.parse_args()
    from loguru import logger
    logger.remove();logging.disable(logging.CRITICAL)
    try:
        config=load(args.config);source=verify_sources()
        if args.check:
            print(json.dumps({'source_revision':source['revision'],'config_sha256':config['sha256'],'configured_servers':[s['id'] for s in config['servers']],'action_adapters':[a['adapter'] for a in config['actions']],'voice_opened':False,'provider_calls':0,'tts_calls':0,'live_authorization_present':(NORMAL/'authorization.json').exists()}));return 0
        authorize(config,source) # Before dotenv, model initialization, MCP, microphone or synthesis.
        asyncio.run(run(config));return 0
    except KeyboardInterrupt:return 130
    except Exception as exc:
        print(json.dumps({'status':'BLOCKED','error_type':type(exc).__name__,'reason':str(exc) if type(exc) in {ValueError,FileNotFoundError} else 'inspect_metadata'}));return 2

if __name__=='__main__':raise SystemExit(main())
