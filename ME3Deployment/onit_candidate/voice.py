"""Standalone normal assistant; no experiment console or stdin command dependency."""
import argparse,asyncio,json,logging,os,sys,uuid
from dataclasses import replace
from datetime import datetime,timezone
from pathlib import Path
from dotenv import load_dotenv
from voice_agent import ROOT,Settings,configure_pi_audio
from me3_core.events import CoreEvents,attach as attach_observers
from onit_candidate.conversation import Context,Conversation,controller_class
from me3_agent.speech import AgentSpeechClients
from speech_mcp.runtime import supervise
from me3_assistant.config import load,DEFAULT
from me3_assistant.guard import authorize,verify_sources,SharedBudget,NORMAL,lock
from me3_assistant.registry import Registry
from me3_assistant.actions import Journal
from me3_assistant.speech import AssistantController
from me3_assistant.voice_policy import install_normal_startup,reserve_open_session,bind_voice_session_stop
from speech_mcp.client import ProviderLimited
SPEECH_MODULE='onit_candidate.asr_server'

def bind_capacity_wait(model,controller,events):
    if hasattr(model,'transport'):
        model.transport.before_capacity_wait=lambda seconds:controller.credit_provider_wait(events.current_generation,seconds)

def runtime_profile(backend):
    from me3_assistant.voice_profile import DECISION_MODEL,PIPER_VOICE
    if backend not in {'groq','google'}:raise ValueError('unsupported_assistant_backend')
    from onit_candidate.reasoning_profile import profile
    reasoning=profile()
    return dict(reasoning_provider=reasoning['provider'],tts_cpu_profile='duplex_2t_no_spin',backend=backend,asr_model='whisper-large-v3-turbo' if backend=='groq' else 'existing_google',reasoning_model=reasoning['model'],tts_provider='piper',tts_model='piper:'+PIPER_VOICE,max_input_seconds=120 if backend=='groq' else 15,max_transcript_chars=4000 if backend=='groq' else 1000)

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


def start_credential_preparation(ordinary,stop_requested,events):
    """Owner-startup authentication only; no inference/reservation or token cache."""
    if getattr(ordinary,'provider',None)!='openai_chatgpt_plan' or stop_requested.is_set():return None
    from me3_openai.transport import Transport,TransportError,auth_failure_code
    if type(ordinary) is not Transport or ordinary.credentials is None:return None
    async def prepare():
        if stop_requested.is_set():raise asyncio.CancelledError()
        events.emit('onit_startup_credentials',stage='started',inference_requests=0)
        try:
            await ordinary.credentials.token() # Existing coalesced, same-account worker; discard secret.
        except asyncio.CancelledError:
            events.emit('onit_startup_credentials',stage='cancelled',inference_requests=0)
            raise
        except Exception as error:
            code=auth_failure_code(error) or 'startup_credentials_failed_no_retry'
            events.emit('onit_startup_credentials',stage='failed',error_code=code,inference_requests=0)
            raise TransportError(code) from None
        events.emit('onit_startup_credentials',stage='ready',inference_requests=0)
        return None
    task=asyncio.create_task(prepare())
    task.add_done_callback(lambda done:None if done.cancelled() else done.exception())
    return task

async def join_credential_preparation(task,stop_requested):
    if task is None:return
    await asyncio.shield(task)
    if stop_requested.is_set():raise asyncio.CancelledError()

async def settle_credential_preparation(ordinary,preparation):
    """Never abandon a shielded refresh worker when owner startup is cancelled."""
    if preparation is None:return False
    if preparation is not None and not preparation.done():preparation.cancel()
    # Existing close owns settlement of Credentials' private to_thread worker.
    # Keep its task alive even if this caller receives another cancellation.
    closing=asyncio.create_task(ordinary.credentials.close())
    interrupted=False
    while not closing.done():
        try:await asyncio.shield(closing)
        except asyncio.CancelledError:interrupted=True
    try:closing.result()
    finally:
        if preparation is not None:
            # Cancellation was requested before close: it cannot start a new worker afterwards.
            while not preparation.done():
                try:await asyncio.shield(preparation)
                except asyncio.CancelledError:interrupted=True
                except Exception:break
            if not preparation.cancelled():preparation.exception()
    return interrupted

async def run(config,observer=None,on_controller=None,backend=None,initial_history=None,stop_requested=None,context=None,on_processor=None,on_call_resources=None,on_call_registry=None,on_reminder_resources=None,warm_floor=False,main_prewarm=False,validated_delivery=False,task_recovery=False,background_core=None,background_core_sha256=None,background_call_held=None):
    if type(task_recovery) is not bool:raise ValueError('task_recovery_boolean_required')
    from onit_candidate.main_prewarm import enabled as validate_main_prewarm
    validate_main_prewarm(main_prewarm)
    if type(validated_delivery) is not bool:raise ValueError('validated_delivery_boolean_required')
    if type(warm_floor) is not bool:raise ValueError('host_floor_switch_boolean_required')
    if warm_floor:
        from me3_floor.runtime import validate_switch
        validate_switch(warm_floor)
    # Public API rechecks the actual files; direct import cannot skip the CLI gate.
    from onit_candidate.identity import verify_candidate
    verify_candidate(ROOT)
    context=context or Context()
    config=load(Path(config['path']));source=verify_sources();grant,limits=authorize(config,source,backend=backend)
    from me3_assistant.backends import selected,check_ready,speech_module,reasoning_class,audio_settings
    backend=selected(grant,backend);check_ready(backend);profile=runtime_profile(backend)
    from onit_candidate.reasoning_profile import require_capacity
    require_capacity(ROOT,'normal')
    if backend!='groq':raise ValueError('onit_candidate_requires_current_Qwen_Groq')
    if stop_requested is None:stop_requested=asyncio.Event()
    if sys.platform!='linux':raise ValueError('normal_voice_requires_Pi_Linux')
    budget=SharedBudget(grant,limits)
    # A session lease covers the action journal and prevents parallel normal sessions.
    with lock(NORMAL/'session.lock'):
        name=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]
        events=Events(NORMAL/'sessions'/name,observer=observer,backend=backend);ordinary=None;music_bus=None;echo=None;stream_binding=None;stream_monitor=None;registry=None;reminder_lane=None;floor_factory=None;main_factory=None;credential_preparation=None;background_delivery=None
        try:
            if backend=='google':load_dotenv(ROOT/'.env',encoding='utf-8-sig')
            from me3_assistant.voice_profile import normal_piper_environment
            piper_environment=await asyncio.to_thread(normal_piper_environment,ROOT)
            os.environ.update(**piper_environment,MAX_SESSION_SECONDS=str(min(config['runtime']['max_session_seconds'],600)),M3_MAX_HUMAN_TURNS='8',PIPECAT_SMART_TURN_LOG_DATA='false')
            settings=normal_settings(config,backend);configure_pi_audio(settings)
            from me3_assistant.acoustic_echo import audio_mode,speaker_echo
            if audio_mode(grant)=='speaker_aec':echo=speaker_echo() # Native availability before model/MCP/audio startup.
            import me3_completion.pipeline as normal_pipeline
            normal_pipeline.ConversationController=controller_class()
            class Speech(AgentSpeechClients):
                max_utterance_seconds=profile['max_input_seconds']
                max_transcript_chars=profile['max_transcript_chars']
                def __init__(self,*args,**kwargs):
                    super().__init__(*args,module=SPEECH_MODULE,**kwargs)
                def require_tts_capacity(self,count):
                    if budget.remaining('tts')<count:
                        budget.exhausted.set();raise ProviderLimited('insufficient_complete_response_allowance')
                async def call(self,role,*args,**kwargs):
                    ordinal=reserve_open_session(budget,role,stop_requested);events.emit('assistant_budget_reserved',role=role,ordinal=ordinal)
                    record=None
                    if role=='asr' and main_factory is not None:
                        generation=args[0] if args else kwargs.get('generation')
                        record=await main_factory.start_asr(generation,ordinal)
                    try:
                        value=await super().call(role,*args,**kwargs)
                        if record is not None:main_factory.asr_complete(record)
                        return value
                    except BaseException:
                        if record is not None:await main_factory.asr_failed(record)
                        raise
            from onit_candidate.reasoning_profile import transport as reasoning_transport
            from me3_assistant.backends import groq_key
            ordinary=reasoning_transport(events.emit,validated_delivery=validated_delivery)
            credential_preparation=start_credential_preparation(ordinary,stop_requested,events)
            if grant.get('local_action_limits',{}).get('music_control',0) or grant.get('profile')=='standing' and grant.get('routine_local_music') is True:
                from me3_assistant.music_audio import MusicBus
                music_bus=MusicBus(events.path.parent/'music-bus.pcm',create=True)
            if grant.get('routine_soundcloud_music') is True:
                from me3_stream.output import SessionBinding
                stream_binding=SessionBinding(ROOT,events,current=lambda:not stop_requested.is_set(),voice=True)
                await stream_binding.open()
                stream_monitor=asyncio.create_task(stream_binding.watch(stop_requested))
            async with Registry(events,config,**({'music_bus':music_bus.path} if music_bus else {}),**({'stream_broker':stream_binding.broker} if stream_binding else {})) as registry:
                registry.budget=budget;registry.journal=Journal(ROOT/'data/assistant-actions.jsonl')
                registry.allowed_actions=[name for name in grant['allowed_actions'] if name!='propose_youtube_control'];registry.session_grant=grant;registry.local_action_directory=NORMAL/'local-action-budgets'
                if on_call_registry:await on_call_registry(registry)
                from onit_candidate.task_continuation import conversation
                reasoner=conversation(ROOT,registry,ordinary,budget,events,context,voice=True,task_recovery=task_recovery,source_revision=source['revision'])
                if warm_floor:
                    from me3_floor.runtime import configure_normal_floor
                    floor_factory=configure_normal_floor(reasoner,source,enabled=True)
                async with Speech(events) as speech:
                    if backend=='groq':
                        from me3_assistant.worker_readiness import prepare_speech_worker
                        await prepare_speech_worker(speech)
                    await join_credential_preparation(credential_preparation,stop_requested)
                    result=normal_pipeline.build_pipeline(settings,events,speech,reasoner)
                    from me3_agent.recovery import attach as attach_recovery
                    attach_recovery(result[3],events)
                    result[3].state.buffer.enable_smoothing(settings.output_rate,settings.output_channels)
                    events.emit('assistant_playback_transition_policy',fade_milliseconds=5,suspension_preserves_queued_PCM=True,cancellation_discards_tail=True,AEC_reference='actual final callback PCM')
                    from me3_assistant.overlap import attach as attach_acknowledgment_surface
                    attach_acknowledgment_surface(result[3],speech,reasoner,events)
                    observe_pipeline(result,settings,events)
                    if music_bus:
                        from me3_assistant.music_audio import attach_music
                        attach_music(result,settings,music_bus,events)
                    if stream_binding:stream_binding.attach_voice(result,settings,echo_available=echo is not None)
                    if on_reminder_resources and echo is not None:
                        reminder_lane=on_reminder_resources(result,settings,echo=echo)
                    if on_call_resources:
                        on_call_resources(result,echo=echo,music_bus=music_bus,stream_binding=stream_binding)
                    if echo:
                        # Only normal speaker mode changes host output granularity.
                        # Capture/VAD/native AEC stay20ms; callback DAC timestamps
                        # align the split final60ms block to each capture frame.
                        result[2].state.configure_input_callback(4)
                        events.emit('assistant_capture_headroom_policy',host_callback_ms=80,AEC_VAD_frame_ms=20,maximum_AEC_queued_frames=12,capture_dispatch_age_limit_seconds=.32,whole_callback_admission=True,original_samples_preserved=True,input_overflow_fatal=True,system_defaults_changed=False,acoustic_qualification=False)
                        result[2].state.configure_output_callback(3)
                        echo.output_callback_periods=3
                        events.emit('assistant_output_headroom_policy',output_callback_ms=60,capture_frame_ms=20,AEC_reference_frame_ms=20,maximum_software_queue_ms=120,active_discontinuity_fatal=True,acoustic_qualification=False)
                        from me3_assistant.acoustic_echo import attach as attach_echo
                        attach_echo(result,settings,echo,events,threaded_capture=True) # After final music mix; before streams open.
                        if stream_binding:stream_binding.bind_reference(echo)
                    runner,worker,transport,processor=result
                    processor.max_pcm_bytes=profile['max_input_seconds']*32000
                    events.emit('assistant_capture_policy',maximum_seconds=processor.max_pcm_bytes/32000,endpoint_uses_final_seconds=15,full_audio_sent_to_asr=True,streaming_transcription=False)
                    processor.controller.max_pcm_bytes=processor.max_pcm_bytes
                    processor.controller.max_transcript_chars=profile['max_transcript_chars']
                    if main_prewarm:
                        from onit_candidate.main_prewarm import MainPrewarm
                        main_factory=MainPrewarm(reasoner,source,processor.controller,on_failure=lambda:speech.mark_failure('main_preparation',None))
                        reasoner.engine.main_prewarm=main_factory
                        main_factory.bind_answer(reasoner)
                    reasoner.controller=processor.controller;processor.controller.max_turns=config['runtime']['max_turns'];processor.controller.capacity_wait_ceiling=min(3600,config['runtime']['max_session_seconds'])
                    if validated_delivery:
                        from onit_candidate.validated_output import bind
                        bind(processor,reasoner,enabled=True)
                    bind_voice_session_stop(reasoner,stop_requested,events)
                    processor.controller.shared_context=context
                    if initial_history and not context.history:context.history=list(initial_history)[-16:]
                    if on_processor:on_processor(processor)
                    if on_controller:on_controller(processor.controller)
                    if background_core is not None:
                        from onit_candidate.background_delivery import bind
                        background_delivery=bind(processor,reasoner,source['revision'],stop_requested,core=background_core,core_sha256=background_core_sha256,call_held=background_call_held)
                    processor.controller.greet=lambda:processor.controller.spawn(processor.controller.speak("What's on your mind today?",0,'normal',greeting=True))
                    install_normal_startup(processor,events)
                    from onit_candidate.voice_startup import gate_startup
                    gate_startup(processor,events)
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
                        if background_delivery is not None:await background_delivery.close()
                        await reasoner.close()
                        events.emit('assistant_session_end',reason=reason,tool_calls=registry.calls,unverified_actions=len(registry.journal.unresolved()))
                    if reason in {'pipeline_fault','runner_error','speech_service_error','runner_cancelled'}:raise RuntimeError('normal_session_failed')
        finally:
            # Also close a bound lane if startup fails before supervise begins.
            if background_delivery is not None:await background_delivery.close()
            credential_cleanup_cancelled=await settle_credential_preparation(ordinary,credential_preparation)
            floor_cleanup_error=None
            if main_factory is not None:
                try:await main_factory.close()
                except BaseException as error:floor_cleanup_error=error
            if floor_factory is not None:
                try:await floor_factory.close()
                except BaseException as error:floor_cleanup_error=error
            if reminder_lane:reminder_lane.close()
            if stream_monitor:stream_monitor.cancel();await asyncio.gather(stream_monitor,return_exceptions=True)
            try:
                if stream_binding:await stream_binding.close(registry)
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
            if floor_cleanup_error is not None:raise floor_cleanup_error
            if credential_cleanup_cancelled:raise asyncio.CancelledError()
