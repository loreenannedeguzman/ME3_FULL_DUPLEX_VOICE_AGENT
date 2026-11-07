"""ME3 product GUI with the current Onit planner. Standby opens no audio, tools or model."""
import argparse,asyncio,json,logging,sys,threading,uuid
from pathlib import Path
from datetime import datetime,timezone
from me3_assistant.gui import Console as BaseConsole,Server as BaseServer,Handler as BaseHandler,WEB
from me3_assistant.guard import NORMAL,SharedBudget,lock,verify_sources
from me3_assistant.config import load
from task_mcp.client import ROOT
from onit_candidate.conversation import Context,Conversation,submit_text
from onit_candidate.identity import verify_candidate

def background_factory(source,enabled=False):
    """Host-only reviewed factory; default-off opens no queue, tools or audio."""
    if type(enabled) is not bool:raise ValueError('background_ready_boolean_required')
    if not enabled:return None,None
    required={
        'onit_candidate/background_notices.py':'e38d82945acfeaac984e615dc536a20a0cb7671434dbc683da6a24001cbe2f04',
        'onit_candidate/background_core_adapter.py':'79234d94b53d12dad08faa2d8d6abd1e183cd85845702976da689e33ab1bb89f',
        'onit_candidate/background_delivery.py':'83c1f9313c0f3ba5703b9635a29446c2e909d27343dc4ad592b7b1248cf6d260',
        'me3_completion/pipeline.py':'295b03673e3cfbee5d1a2f69a73a2d25d8b6075f3e4bb86b38a6f332d632cc35',
        'onit_candidate/runtime_view.py':'e8b02cef017da7fc11e4d4e70b629fbfb713ad3342b0928577f81f2c1a646e9e',
    }
    if type(source) is not dict or any(source.get('source_hashes',{}).get(n)!=h for n,h in required.items()):raise ValueError('reviewed_background_source_membership_required')
    from onit_candidate.background_core_adapter import BackgroundCoreAdapter
    return BackgroundCoreAdapter,required['onit_candidate/background_core_adapter.py']

class Console(BaseConsole):
    def __init__(self,*args,warm_floor=False,validated_delivery=False,task_recovery=False,background_ready=False,**kw):
        if type(task_recovery) is not bool:raise ValueError('task_recovery_boolean_required')
        self.task_recovery=task_recovery
        if type(background_ready) is not bool:raise ValueError('background_ready_boolean_required')
        if background_ready and validated_delivery is not True:raise ValueError('background_requires_validated_delivery')
        self.background_ready=background_ready
        if type(validated_delivery) is not bool:raise ValueError('validated_delivery_boolean_required')
        self.validated_delivery=validated_delivery
        if type(warm_floor) is not bool:raise ValueError('host_floor_switch_boolean_required')
        if warm_floor:
            from me3_floor.runtime import validate_switch
            validate_switch(warm_floor)
        self.warm_floor=warm_floor
        self.shared=Context();self.processor=None;self.voice_armed=False;self.session_replies={}
        # CORE_UNBOUND until reviewed actual-Onit core and its source pin exist.
        self.background_core_factory=None;self.background_core_sha256=None
        self.reasoning_error=None;self.asr_network_error=None;self.audio_error=None
        self.capacity_error='Reasoning service capacity is unavailable. No automatic retry. Completed actions remain unchanged.'
        super().__init__(*args,**kw)
        self.backend='groq';self.notice='';self._candidate_hashes=verify_candidate(ROOT)
        self.background_core_factory,self.background_core_sha256=background_factory(self.source,self.background_ready)
        from me3_whatsapp.call_session import HostCallSession
        self.calls=HostCallSession(self)
        self.reminder_lane=None;self.reminder_delivery=None
    @property
    def history(self):return self.shared.history
    @history.setter
    def history(self,value):self.shared.history=value
    def bind_controller(self,controller):
        self.controller=controller;owned={}
        def display(generation,text,state):
            record=owned.get(generation)
            if record is None:
                anchor=next((r for r in reversed(controller.history) if r.get('type')=='user_input'),None)
                if anchor is None:return
                record={'anchor':anchor,'text':text,'state':state};owned[generation]=record
                self.session_replies[id(anchor)]=record
            record.update(text=text,state=state)
        controller.display_reply=display
    def display_conversation(self,history):
        # Display lifetime is the session, not the model's last16 rows. The
        # explicit Clear command remains the only operation that erases it.
        self.shared.observe_history(history);messages=[];projected=False
        from me3_assistant.gui import conversation
        for i,r in enumerate(self.shared.display_history()):
            if r.get('type')=='user_input':projected=False
            if r.get('type')=='model_output' and r.get('background_notice') is True:
                messages.extend({**m,'id':str(i)} for m in conversation([r]));continue
            if r.get('control_state') or r.get('type')=='model_output' and projected:continue
            if r.get('type') not in {'user_input','model_output'}:continue
            messages.extend({**m,'id':str(i)} for m in conversation([r]))
            record=self.session_replies.get(id(r))
            if record is not None:
                messages.append({'id':str(i)+'-reply','role':'assistant','text':record['text'],'state':record['state']});projected=True
        return messages
    def gate(self,mode,backend=None):
        if verify_candidate(ROOT)!=self._candidate_hashes:raise ValueError('restart_after_candidate_change')
        if backend not in {None,'groq'}:raise ValueError('candidate_uses_current_Qwen_Groq')
        grant,limits=super().gate(mode,backend='groq')
        from onit_candidate.reasoning_profile import require_capacity
        require_capacity(ROOT,mode)
        return grant,limits
    def bind_processor(self,processor):self.processor=processor;self.voice_armed=False
    def bind_reminder_resources(self,result,settings,*,echo):
        from me3_assistant.reminder_audio import attach
        lane=attach(result,settings,blocked=lambda:self.calls.held or self.stop_event is None or self.stop_event.is_set(),echo=echo)
        self.reminder_lane=lane
        if self.reminder_delivery:self.reminder_delivery.bind(lane)
        return lane
    async def bind_call_registry(self,registry):
        # Registry exists before the voice pipeline/devices are opened. Each new
        # voice owner rebinds authority; call commit/end use a fresh private MCP
        # client after this ordinary Registry has closed.
        if not any(s.get('id')=='calling' and s.get('module')=='me3_whatsapp.call_server' for s in registry.config.get('servers',[])):return
        from me3_whatsapp.call_client import configure_call_session
        from me3_whatsapp.call_recovery import audit
        async def recover(record,session):return await audit(ROOT,record,session)
        await configure_call_session(registry,self.calls,ROOT,
            on_result=getattr(self,'_call_result_callback',None),
            verify_recovery=getattr(self,'_call_recovery_verifier',recover))
    async def initialize_calls(self):
        # Standby bootstrap reads only local durable authorization/recovery.
        # Its current(False) authority cannot authorize a new outgoing call.
        from types import SimpleNamespace
        class Events:
            def emit(inner,event,**fields):self.observe(event,**fields)
        bootstrap=SimpleNamespace(config=self.config,events=Events(),current=lambda g:False)
        await self.bind_call_registry(bootstrap)

    def contact_commit_scope(self,fields):
        # UI projection only. The action handler still owns durable settlement;
        # this grants no send, retry, verification or status-read authority.
        from me3_whatsapp.contact_message_action import ContactMessage
        controller=getattr(self,'controller',None)
        provider=getattr(controller,'provider',None)
        engine=getattr(provider,'engine',None)
        gate=getattr(engine,'gateway',None)
        registry=getattr(engine,'registry',None)
        g=fields.get('generation');rid=fields.get('request_id')
        owner_generation=getattr(provider,'active_generation',None)
        if (self.mode!='voice' or controller is None or provider is None or engine is None or gate is None or registry is None
            or provider.context is not self.shared or provider.controller is not controller
            or type(g) is not int or type(owner_generation) is not int
            or not controller.current(owner_generation) or not provider.current(owner_generation) or not engine.current(g)
            or gate.generation!=g or gate.registry is not registry or gate.actions.registry is not registry
            or engine.task is not asyncio.current_task() or engine.task is None
            or self.stop_event is None or self.stop_event.is_set()
            or not self.task or self.task.done()
            or fields.get('tool')!='commit_contact_message'
            or not isinstance(rid,str) or not rid.startswith('assistant-') or len(rid)!=42
            or any(c not in '0123456789abcdef' for c in rid[10:])):return None
        context=getattr(registry,'communication_context',None);key=gate.pending_key
        if (not isinstance(context,dict) or context.get('generation')!=g or context.get('goal')!=gate.goal
            or not key or key[0]!=gate.goal or self.shared.goal_id!=gate.goal
            or gate.actions.pending is not None):return None
        row=gate.ledger.rows.get(key,{})
        action=row.get('action');op=row.get('operation_id')
        if action!='propose_contact_message' or row.get('state')!='WAITING_CONFIRMATION':return None
        record=gate.journal.records.get(op,{})
        adapter=gate.actions.adapters.get(action)
        server=fields.get('server');session=registry.sessions.get(server)
        if (not isinstance(adapter,ContactMessage) or adapter.registry is not registry or adapter.server!=server
            or action not in gate.actions.allowed or not session or session[1].get('module')!='me3_whatsapp.server'
            or (server,'commit_contact_message') not in registry.schemas
            or record.get('state')!='DISPATCHED' or record.get('operation_id')!=op or record.get('action')!=action
            or not isinstance(record.get('expected_digest'),str) or len(record['expected_digest'])!=64
            or any(c not in '0123456789abcdef' for c in record['expected_digest'])):return None
        return (rid,server,g,provider,owner_generation,engine.task,self.shared.conversation_id,self.shared.revision,
                self.source['revision'],registry.communication_owner,id(context),key,op,record['expected_digest'])

    def observe(self,event,**fields):
        if event=='assistant_rpc_start':
            self._contact_commit_error_scope=self.contact_commit_scope(fields)
        elif event in {'assistant_rpc_error','assistant_rpc_end','assistant_rpc_canceled'}:
            scope=getattr(self,'_contact_commit_error_scope',None)
            self._contact_commit_error_scope=None
            if event=='assistant_rpc_error' and scope is not None and scope==self.contact_commit_scope(fields):
                # The raw Registry error remains in the session evidence. Defer
                # only its UI fatal projection while the exact existing handler
                # records UNCERTAIN and returns its no-repeat status response.
                event='assistant_rpc_action_outcome_pending';fields={**fields,'state':'failed'}
        if event in {'background_start_adoption_unverified','background_readiness_notice','background_notice_unverified','background_notice_lane_failed'}:
            self.notice='The background task readiness or announcement could not be verified. No action was retried; use a fresh status check.'
        if event=='onit_task_intent_accepted':
            labels = {"I'll check that.", "I'll check the news.", "I'll work on getting that running.", "I'll try to stop that service.", "I'll try to stop those services.", "I'll check the weather.", "I'll look that up.", "I'll check your calendar.", "I'll check your local music.", "I'll check the saved file.", "I'll check the saved files.", "I'll work on that file.", "I'll work on those files.", "I'll work through that task."}
            g=fields.get('generation');controller=getattr(self,'controller',None)
            if (type(g) is int and controller is not None and controller.current(g)
                and self.shared.goal_id is not None and self.shared.goal_id==fields.get('goal_id')
                and isinstance(fields.get('text'),str) and fields['text'] in labels
                and fields.get('substantive_answer') is False and fields.get('action_authority') is False
                and type(fields.get('additional_model_requests')) is int and fields['additional_model_requests']==0):
                super().observe('assistant_goal_progress',generation=g,goal_id=self.shared.goal_id,tool=fields['text'],state='started')
        if event=='speaker_aec_fault':
            part='Microphone capture' if fields.get('stage')=='capture' else 'Audio output' if fields.get('stage')=='playback' else 'Audio'
            self.audio_error=part+' was interrupted. The conversation stopped; earlier completed actions remain unchanged. No automatic retry.'
        if event=='assistant_asr_worker_readiness_failed':
            self.asr_network_error=('Speech recognition could not connect during startup. The microphone was not opened. No automatic retry.' if fields.get('stage') in {'dns','tcp_tls'} else 'Speech recognition was not ready during startup. The microphone was not opened. No automatic retry.')
        if event=='mcp_request_error' and fields.get('role')=='asr' and fields.get('error_type') in {'TimeoutError','ConnectionTimeoutError','ClientConnectorError','ClientConnectorDNSError','ClientConnectorCertificateError','ClientConnectorSSLError'}:
            self.asr_network_error=('Speech recognition did not respond in time. No transcript was obtained for this request. Earlier completed actions remain unchanged; no automatic retry.' if fields.get('error_type')=='TimeoutError' else 'Speech recognition could not connect. No transcript was obtained for this request. Earlier completed actions remain unchanged; no automatic retry.')
        if event=='onit_provider_request_failed' and fields.get('provider')=='google' and fields.get('error_type') in {'TimeoutError','SocketTimeoutError','ConnectionTimeoutError'}:
            self.reasoning_error='Reasoning service did not respond in time. The request stopped; no automatic retry. Earlier completed actions remain unchanged.'
        if event=='onit_provider_rejection' and fields.get('provider')=='google':
            status=fields.get('status')
            if type(status) is int and 500<=status<=599 and self.reasoning_error is None:
                self.reasoning_error=('Reasoning service returned HTTP '+str(status)+'. This request stopped; no automatic retry. Completed actions remain unchanged.')
        if event=='onit_provider_capacity_rejection':
            self.voice_failure='capacity'
            scope=fields.get('limit_scope')
            label={'tokens_per_day':'daily token limit','requests_per_day':'daily request limit','tokens_per_minute':'token rate limit','input_tokens_per_minute':'input-token rate limit','output_tokens_per_minute':'output-token rate limit','requests_per_minute':'request rate limit'}.get(scope,'service capacity limit')
            self.capacity_error='Reasoning service reached its '+label+'. No automatic retry. Completed actions remain unchanged.'
        # The ASR client validates identity/release and classifies a bounded
        # clean timeout before deciding whether this read-only turn is fatal.
        if event=='mcp_request_error' and fields.get('role')=='asr':
            self._asr_deferred_error=(fields.get('generation'),fields.get('request_id'))
        if event in {'m7_clean_timeout_recoverable','m7_clean_transport_recoverable'}:
            key=(fields.get('generation'),fields.get('request_id'))
            controller=getattr(self,'controller',None)
            if (key==getattr(self,'_asr_deferred_error',None)
                and type(key[0]) is int and isinstance(key[1],str)
                and type(fields.get('count')) is int and 1<=fields['count']<=2
                and fields.get('automatic_retry') is False
                and (event=='m7_clean_timeout_recoverable' or fields.get('transport_recovery_ready') is True)
                and controller is not None and controller.current(key[0])
                and self.stop_event is not None and not self.stop_event.is_set()):
                self._asr_deferred_error=None;self.asr_network_error=None
                if self.voice_failure is None and self.reasoning_error is None and self.audio_error is None:
                    self.public_error=None
                self.notice='No transcript was obtained. Please say your request again; nothing was retried.'
        if event=='onit_voice_listening':self.voice_armed=True
        if event=='onit_gateway_progress':
            fields={**fields,'tool':fields.get('action','Task')}
            super().observe('assistant_goal_progress',**fields)
        super().observe(event,**fields)
        if event=='onit_voice_listening':self.state='listening'
        if self.calls.held:
            self.voice_armed=False;self.state='call_'+self.calls.state.lower()
        if event=='onit_reply_continuation_pending' and self.stop_event and not self.stop_event.is_set():self.state='processing'
        if event in {'empty_transcription','completion_background_error','onit_candidate_failed','mcp_request_error','speech_service_failed','mcp_cancellation_settle_timeout','pipeline_fault'} and not (event=='mcp_request_error' and fields.get('role')=='asr'):
            self.public_error='The request stopped. Completed actions remain unchanged; no automatic retry.'
            if self.stop_event:self.stop_event.set()
        # Base turn/cleanup events must not overwrite the actual capacity cause.
        if self.voice_failure=='capacity':self.public_error=self.capacity_error
        elif self.reasoning_error:self.public_error=self.reasoning_error
        elif self.asr_network_error:self.public_error=self.asr_network_error
        elif self.audio_error:self.public_error=self.audio_error
    def snapshot(self,checks=None):
        result=super().snapshot(checks)
        if self.voice_failure=='capacity':result['error']=self.capacity_error
        elif self.reasoning_error:result['error']=self.reasoning_error
        elif self.asr_network_error:result['error']=self.asr_network_error
        elif self.audio_error:result['error']=self.audio_error
        from onit_candidate.reasoning_profile import profile
        result['reasoning_profile']=profile()
        result['engine']='full Onit candidate';result['conversation_id']=self.shared.conversation_id
        result['task_context']={'goal_id':self.shared.goal_id,'parent_goal_id':self.shared.parent_goal_id,'revision':self.shared.revision,'state':self.shared.state}
        task_provider=getattr(self.processor.controller,'provider',None) if self.processor is not None else self.router
        task_state=getattr(task_provider,'task_continuation',None)
        result['task_recovery']={'enabled':self.task_recovery,'armed_next':bool(task_state and task_state.armed_next),
            'selection_pending':bool(task_state and task_state.armed is not None and task_state.selected_reference is not None),
            'can_track':bool(task_state and task_state.armed is None and not task_state.armed_next and (task_state.active is None or task_state.active['state'] in {'completed','canceled'}))}
        result['can_send']=result['can_send'] or bool(self.mode=='voice' and result['active'] and self.voice_armed and self.stop_event and not self.stop_event.is_set())
        result['artifact_preview_open']=self.shared.artifact_preview.notification() or self.shared.runtime_preview.notification()
        result['artifact_preview_close']=self.shared.preview_close.notification(self.source['revision'])
        monitor_notice=self.shared.service_monitor.notification()
        result['service_monitor_notice']=monitor_notice
        if monitor_notice and result.get('error') is None and not result.get('notice'):result['notice']=monitor_notice['text']
        result['call']=self.calls.snapshot()
        if result['call']['held']:
            result['can_send']=False;result['state']='call_'+result['call']['state'].lower()
            result['active']=True
        return result
    async def command(self,data):
        action=data.get('action')
        self.calls.admit(action)
        if action=='preview_close_ack':
            from me3_assistant.guard import verify_sources
            from onit_candidate.identity import verify_candidate
            source=verify_sources(ROOT)
            if source['revision']!=self.source['revision'] or verify_candidate(ROOT)!=self._candidate_hashes:raise ValueError('preview_close_source_changed')
            if set(data)!={'action','close_id','view_id','conversation_id'}:raise ValueError('exact_preview_ack_fields_required')
            return self.shared.preview_close.acknowledge({k:v for k,v in data.items() if k!='action'},source['revision'])
        if action in {'stop','cancel','clear'}:
            self.shared.artifact_preview.clear();self.shared.runtime_preview.clear(close=action=='clear')
        elif action=='send':
            self.shared.artifact_preview.revoke_open();self.shared.runtime_preview.revoke_open()
        if action in {'track_task','track_next_task','cancel_task_tracking','task_list','task_reference','select_task','finish_task','finish_saved_task'}:
            from onit_candidate.task_owner import command
            return await command(self,data,ROOT)
        if action=='send' and self.mode=='voice':
            text=data.get('text')
            if not isinstance(text,str) or not 1<=len(text.strip())<=4000:raise ValueError('message_must_be_1_to_4000_characters')
            if not self.voice_armed or not self.processor or not self.task or self.task.done() or self.stop_event.is_set():raise ValueError('wait_for_listening')
            await submit_text(self.processor,text.strip());return {'ok':True}
        if action=='clear':
            expected=data.get('expected_conversation_id')
            if expected is not None and expected!=self.shared.conversation_id:raise ValueError('conversation_changed_nothing_cleared')
            if self.calls.held or any(t is not None and not t.done() for t in (self.task,self.turn)):raise ValueError('stop_before_new_conversation')
            # This short bounded durable write and Context swap execute without
            # an await, so another Start/Send cannot race an archive/CAS reset.
            from onit_candidate.conversation_archive import archive
            history=list(self.controller.history if self.controller is not None else self.history)
            messages=self.display_conversation(history)
            if self.pending:messages.append(dict(role='assistant',text=self.pending['text']))
            monitor_context=self.shared
            await monitor_context.service_monitor.stop()
            if self.shared is not monitor_context or self.calls.held or any(t is not None and not t.done() for t in (self.task,self.turn)):
                raise ValueError('conversation_changed_during_monitor_settlement')
            archived=archive(ROOT,dict(active=False,call_held=False,conversation_id=self.shared.conversation_id,source_revision=self.source['revision'],messages=messages))
            result=await super().command(data)
            self.shared=Context();self.session_replies={};self.reasoning_error=None;self.asr_network_error=None;self.audio_error=None
            self.voice_failure=None;self.voice_armed=False;self.processor=None;self.router=None;self.controller=None;self.pending=None
            self.task=None;self.turn=None;self.stop_event=None;self.ready=None;self.turns=0;self.generation+=1
            self.mode='standby';self.state='stopped';self.notice='New conversation ready. Previous conversation archived.' if archived['archived'] else 'New conversation ready.'
            return dict(result,conversation_id=self.shared.conversation_id,archive=archived)
        result=await super().command(data)
        if action=='ack_reminder' and self.reminder_delivery:self.reminder_delivery.cancel(data.get('reminder_id'))
        if action in {'start_voice','start_text'}:self.reasoning_error=None;self.asr_network_error=None;self.audio_error=None
        return result
    async def cancel(self):
        if self.router:self.router.interrupt();self.router.pending=None
        await super().cancel()
    async def voice(self):
        from onit_candidate.voice import run
        try:
            background_core=self.background_core_factory(self.shared.conversation_id,self.source['revision']) if callable(self.background_core_factory) else None
            await run(self.config,observer=self.observe,on_controller=self.bind_controller,backend='groq',
                      context=self.shared,on_processor=self.bind_processor,stop_requested=self.stop_event,
                      on_call_resources=self.calls.bind_voice_resources,on_call_registry=self.bind_call_registry,
                      on_reminder_resources=self.bind_reminder_resources,warm_floor=self.warm_floor,validated_delivery=self.validated_delivery,task_recovery=self.task_recovery,background_core=background_core,background_core_sha256=self.background_core_sha256,background_call_held=lambda:self.calls.held)
        except asyncio.CancelledError:raise
        except Exception as exc:
            self.public_error=(self.capacity_error if self.voice_failure=='capacity' else self.reasoning_error or self.asr_network_error or self.audio_error or 'Voice stopped before the request finished. Check its status before starting again.')
            self.notice='Voice ended: '+type(exc).__name__+'. No automatic retry.'
            self.observe('gui_voice_failed',reason=type(exc).__name__)
        finally:
            try:
                if self.reminder_lane:self.reminder_lane.close()
                if self.reminder_delivery:self.reminder_delivery.drain()
            finally:
                self.reminder_lane=None
                self.controller=None;self.processor=None;self.voice_armed=False;self.state='stopped'
                self.observe('gui_voice_closed')
    async def text(self,grant,limits):
        from me3_assistant.registry import Registry
        from me3_assistant.actions import Journal
        from me3_assistant.backends import groq_key
        from onit_candidate.reasoning_profile import transport as reasoning_transport
        from onit_candidate.voice import Events
        name='onit-text-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]
        events=None;transport=None;music_output=None;monitor=None;stream_binding=None;stream_monitor=None;registry=None
        try:
            with lock(NORMAL/'session.lock'):
                events=Events(NORMAL/'sessions'/name,observer=self.observe,backend='groq')
                transport=reasoning_transport(events.emit);budget=SharedBudget(grant,limits)
                if grant.get('routine_soundcloud_music') is True:
                    from me3_stream.output import SessionBinding
                    stream_binding=SessionBinding(ROOT,events,current=lambda:not self.stop_event.is_set(),voice=False)
                from me3_assistant.standing_music import authorized
                if authorized(grant):
                    from me3_assistant.typed_music import TypedMusicOutput
                    music_output=TypedMusicOutput(events.path.parent/'music-bus.pcm',events,external_pcm=stream_binding.pcm if stream_binding else None)
                    async def watch():
                        while not music_output.fault.is_set():await asyncio.sleep(.05)
                        events.emit('onit_candidate_failed',reason='music_output');self.stop_event.set()
                    monitor=asyncio.create_task(watch())
                if stream_binding:
                    if music_output is None:raise ValueError('SoundCloud_requires_owned_output')
                    stream_binding.typed_output=music_output
                    await stream_binding.open()
                    stream_monitor=asyncio.create_task(stream_binding.watch(self.stop_event))
                async with Registry(events,self.config,**({'music_bus':music_output.bus.path} if music_output else {}),**({'stream_broker':stream_binding.broker} if stream_binding else {})) as registry:
                    if music_output:registry.ensure_music_output=music_output.ensure_open
                    registry.budget=budget;registry.journal=Journal(ROOT/'data/assistant-actions.jsonl')
                    registry.allowed_actions=[n for n in grant['allowed_actions'] if n!='propose_youtube_control'];registry.session_grant=grant;registry.local_action_directory=NORMAL/'local-action-budgets'
                    from onit_candidate.task_continuation import conversation
                    self.router=conversation(ROOT,registry,transport,budget,events,self.shared,task_recovery=self.task_recovery,source_revision=self.source['revision'])
                    owner=self
                    class Clock:
                        @property
                        def current(inner):return owner.generation
                    class Control:
                        clock=Clock()
                        def current(inner,g):return g==owner.generation and not owner.stop_event.is_set()
                    self.router.controller=Control();self.state='ready';self.ready.set();self.notice=''
                    try:
                        async with asyncio.timeout(self.config['runtime']['max_session_seconds']):await self.stop_event.wait()
                    finally:await self.cancel();await self.router.close()
        except Exception as exc:
            self.public_error=self.capacity_error if self.voice_failure=='capacity' else self.reasoning_error or 'The conversation stopped. No automatic retry.';self.notice=type(exc).__name__
        finally:
            self.router=None;self.state='stopped'
            if monitor:monitor.cancel();await asyncio.gather(monitor,return_exceptions=True)
            if stream_monitor:stream_monitor.cancel();await asyncio.gather(stream_monitor,return_exceptions=True)
            try:
                if stream_binding:await stream_binding.close(registry)
            finally:
                try:
                    if music_output:await music_output.aclose()
                finally:
                    try:
                        if transport:await transport.close()
                    finally:
                        if events:events.emit('gui_text_closed');events.close()
    async def answer(self,g):
        try:
            # Same60s cumulative observed-wait allowance as voice, then normal
            # processing. Real provider failure still ends the live packet.
            async with asyncio.timeout(min(3600,self.config['runtime']['max_session_seconds'])):answer=await self.router.answer(list(self.history))
            if g==self.generation and not self.stop_event.is_set():
                self.pending={'id':uuid.uuid4().hex,'text':answer};self.state='awaiting_display'
        except asyncio.CancelledError:raise
        except Exception as exc:
            self.shared.state='failed';self.observe('onit_candidate_failed',reason=type(exc).__name__)

class Handler(BaseHandler):
    def send_header(self,keyword,value):
        # Only the normal GUI may embed its authenticated opaque artifact frame.
        if keyword.lower()=='content-security-policy' and "default-src 'self'" in value:
            from onit_candidate.artifact_gui import PARENT_CSP
            value=PARENT_CSP
        return super().send_header(keyword,value)
    def do_POST(self):
        from onit_candidate.artifact_gui import handle_runtime_post
        if handle_runtime_post(self):return
        if self.path not in {'/api/call/end','/api/call/status'}:return super().do_POST()
        import secrets
        allowed={f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}'}
        if (not self.host_ok() or not self.session_ok() or self.headers.get('Origin') not in allowed
            or not secrets.compare_digest(self.headers.get('X-ME3-CSRF',''),self.server.token)):
            return self.reply(403,{'error':'request_rejected'})
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=256 or self.headers.get('Content-Type')!='application/json':raise ValueError('invalid_call_end_request')
            data=json.loads(self.rfile.read(size))
            if not isinstance(data,dict) or set(data)!={'operation_id'}:raise ValueError('exact_call_operation_required')
            calls=self.server.console.calls
            action='end_call' if self.path=='/api/call/end' else 'check_call_status'
            request=calls.gui_request(data['operation_id'],action=action)
            command=calls.end_call if action=='end_call' else calls.check_call_status
            return self.reply(200,self.server.dispatch(command(request)))
        except ValueError as exc:return self.reply(400,{'error':str(exc)})
        except Exception:return self.reply(503,{'error':'call_status_unverified_no_retry' if self.path=='/api/call/status' else 'call_end_unverified_no_retry'})
    def do_GET(self):
        from onit_candidate.artifact_gui import handle_get
        if handle_get(self):return
        from urllib.parse import urlsplit
        path=urlsplit(self.path).path
        if path in {'/','/app.js','/youtube.js'}:
            if not self.host_ok():return self.reply(403,{'error':'host_rejected'})
            if path=='/':
                from onit_candidate.product_html import HTML
                return self.reply(200,HTML.replace('__CSRF__',self.server.token).encode('utf-8'),'text/html; charset=utf-8',cookie=True)
            if path=='/youtube.js':return self.reply(404,{'error':'not_found'})
            return self.reply(200,Path(__file__).with_name('app.js').read_bytes(),'text/javascript; charset=utf-8')
        return super().do_GET()
class Server(BaseServer):
    def __init__(self,address,console,loop):
        super().__init__(address,console,loop)
        self.RequestHandlerClass=Handler
async def serve(config,port=8765,resume_context=None,*,warm_floor=False,validated_delivery=False,task_recovery=False,background_ready=False):
    verify_candidate(ROOT)
    from me3_assistant.reminders import Store
    path=ROOT/'data/reminders.sqlite3'
    # Same lease as the legacy GUI: one persistent scheduler, independent of
    # voice Stop. Only its actual owner may clear scheduler health on exit.
    with lock(path.with_suffix('.scheduler.lock')):
        store=Store(path);store.initialize()
        try:await serve_owned(config,port,resume_context,store,warm_floor=warm_floor,validated_delivery=validated_delivery,task_recovery=task_recovery,background_ready=background_ready)
        finally:store.shutdown()

async def serve_owned(config,port,resume_context,store,*,warm_floor=False,validated_delivery=False,task_recovery=False,background_ready=False):
    console=Console(config,verify_sources(),store,warm_floor=warm_floor,validated_delivery=validated_delivery,task_recovery=task_recovery,background_ready=background_ready)
    from me3_assistant.reminder_delivery import Delivery
    console.reminder_delivery=Delivery(ROOT/'data/reminder-delivery.sqlite3',store,console)
    await console.initialize_calls() # Durable unresolved calls fence Start before HTTP is exposed.
    if resume_context is not None:
        from onit_candidate.context_resume import restore_snapshot
        restore_snapshot(ROOT,resume_context,console,console.source['revision'])
    server=Server(('127.0.0.1',port),console,asyncio.get_running_loop())
    # One normal ME3 interface; no separate check GUI or second planner owner.
    # Qualification status remains in evidence; standby starts no voice session.
    thread=asyncio.create_task(asyncio.to_thread(server.serve_forever,poll_interval=.2))
    async def tick():
        while True:
            try:console.reminder_delivery.tick()
            except Exception:console.notice='Reminder delivery needs checking. Due alerts remain visible; no sound retry is claimed.'
            await asyncio.sleep(1)
    ticker=asyncio.create_task(tick())
    print(json.dumps(dict(gui_url='http://127.0.0.1:'+str(port),engine='full Onit candidate',standby=True,microphone=False,provider_calls=0)),flush=True)
    try:await asyncio.Event().wait()
    finally:
        try:await console.stop()
        finally:
            await console.shared.service_monitor.stop()
            ticker.cancel();await asyncio.gather(ticker,return_exceptions=True)
            try:console.reminder_delivery.drain()
            finally:
                await asyncio.to_thread(server.shutdown);server.server_close();await thread
def arguments(argv=None):
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=8765)
    p.add_argument('--resume-context',type=Path,help='Private one-use host-signed conversation-text snapshot')
    p.add_argument('--warm-floor',action='store_true',help='Host-only advisory floor opt-in; requires reviewed source gate')
    p.add_argument('--validated-delivery',action='store_true',help='Host-only reviewed validated delivery opt-in; no microphone activation')
    p.add_argument('--task-recovery',action='store_true',help='Host-only reviewed saved-task controls; disabled by default, no automatic replay')
    p.add_argument('--background-ready',action='store_true',help='Reviewed owned-service readiness notices; disabled by default, no automatic launch or action replay')
    args=p.parse_args(argv)
    if args.port!=8765:raise ValueError('use_ME3_Conversation_port_8765')
    return args

def main():
    args=arguments()
    from loguru import logger
    logger.remove();logging.disable(logging.CRITICAL)
    asyncio.run(serve(load(),args.port,args.resume_context,warm_floor=args.warm_floor,validated_delivery=args.validated_delivery,task_recovery=args.task_recovery,background_ready=args.background_ready))
if __name__=='__main__':main()
