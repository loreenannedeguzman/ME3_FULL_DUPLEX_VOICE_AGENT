"""One in-memory voice/text conversation owner; Onit is the only planner."""
import asyncio,copy,hashlib,json,re,uuid
from contextlib import AsyncExitStack
from pathlib import Path
from task_mcp.reasoner import CONFIRM,CANCEL,latest_text,normalized,prompt_delivered
from onit_candidate.protocol import CandidateError
from onit_candidate.confirmation import confirmation_intent

def candidate_session_end_kind(raw):
    from me3_assistant.voice_policy import voice_session_end_kind
    kind=voice_session_end_kind(raw)
    if kind:return kind
    if not isinstance(raw,str) or len(raw)>256:return None
    trimmed=re.sub(r'^\s*(?:okay|ok|alright|all right)(?:\s*[,!.]\s*|\s+)', '',raw,count=1,flags=re.I)
    return voice_session_end_kind(trimmed) if trimmed!=raw else None

def ends_candidate_session(raw):
    return candidate_session_end_kind(raw) is not None

def text_of(row):
    return ' '.join(p['text'] for p in row.get('content',[]) if p.get('type')=='text' and isinstance(p.get('text'),str))

def onit_history(rows):
    """Only conversational text enters native task/response history.

    Host control rows stay in the original history/evidence and travel only as
    structured delivery metadata. No synthetic annotation impersonates speech.
    """
    pairs=[];question=None;answers=[]
    for row in rows:
        if row.get('background_notice') is True:continue
        if row.get('type')=='user_input':
            if question is not None:pairs.append(dict(task=question,response='\n'.join(answers)))
            question=text_of(row);answers=[]
        elif row.get('type')=='model_output' and question is not None and not row.get('control_state'):
            answers.append(text_of(row))
    if question is not None:pairs.append(dict(task=question,response='\n'.join(answers)))
    return pairs

def delivery_metadata(rows):
    result=[];turn=None
    for row in rows:
        if row.get('background_notice') is True:continue
        if row.get('type')=='user_input':
            turn=dict(turn=len(result),state='no_reply',submitted_characters=0,complete_reply_recorded=False);result.append(turn)
        elif row.get('type')=='model_output' and turn is not None:
            control=row.get('control_state')
            if control:turn['state']=control if control in {'failed','cancelled'} else 'interrupted'
            else:
                turn['submitted_characters']+=len(text_of(row))
                turn['complete_reply_recorded']=not bool(row.get('partial_output'))
                turn['state']='partial' if row.get('partial_output') else 'complete'
    return result

class Context:
    def __init__(self):
        self.conversation_id=uuid.uuid4().hex;self._history=[];self.transcript=[];self.goal_id=None
        self.parent_goal_id=None;self.revision=0;self.state='idle';self.last_result=None;self.clarification=None
        from onit_candidate.retained import RetainedReplies
        self.retained=RetainedReplies()
        from onit_candidate.productivity_records import ProductivityRecords
        self.productivity_records=ProductivityRecords()
        from onit_candidate.artifact_offer import PreviewOffers
        self.artifact_preview=PreviewOffers(self)
        from onit_candidate.runtime_view import RuntimePreview
        self.runtime_preview=RuntimePreview(self)
        from onit_candidate.artifact_offer import PreviewClosures
        self.preview_close=PreviewClosures(self)
        from onit_candidate.service_monitor import ServiceMonitor
        self.service_monitor=ServiceMonitor(self)
    def observe_history(self,rows):
        # Session display retains original row identities independently of the
        # bounded prompt window. No copied intended speech enters model memory.
        known={id(r) for r in self.transcript}
        for row in rows:
            if id(row) not in known:self.transcript.append(row);known.add(id(row))
    @property
    def history(self):return self._history
    @history.setter
    def history(self,value):
        self.observe_history(self._history)
        # A completed segmented reply removes its temporary prefix row. Drop
        # that row only while its user anchor remains in the working window;
        # trimming an old window must never erase earlier displayed exchanges.
        live={id(r) for r in value};anchor=None;remove=set()
        for row in self._history:
            if row.get('type')=='user_input':anchor=row
            if row.get('partial_output') and id(row) not in live and anchor is not None and id(anchor) in live:remove.add(id(row))
        if remove:self.transcript[:]=[r for r in self.transcript if id(r) not in remove]
        self._history=value;self.observe_history(value)
    def display_history(self):
        self.observe_history(self._history)
        return list(self.transcript)
    def begin(self):
        # A correction to unfinished work keeps the same durable intent owner.
        # A later completed-turn goal gets a linked identity, so a deliberate
        # new play/stop/play command is not suppressed as a duplicate forever.
        if self.goal_id is None or self.state in {'completed','failed','canceled','closed'}:
            self.parent_goal_id=self.goal_id;self.goal_id=uuid.uuid4().hex
        self.revision+=1;self.state='running';return self.goal_id
    def interrupt(self):
        if self.goal_id is not None and self.state in {'running','responding','waiting_confirmation','interrupted'}:self.state='interrupted'
    def metadata(self,gateway=None):
        from onit_candidate.artifact_offer import current_preview
        rows=list(gateway.ledger.load().values())[-12:] if gateway else []
        return dict(conversation_id=self.conversation_id,goal_id=self.goal_id,parent_goal_id=self.parent_goal_id,
                    revision=self.revision,state=self.state,pending_intent=copy.deepcopy(self.clarification),runtime_view=self.runtime_preview.metadata(),service_monitor=self.service_monitor.metadata(),current_preview=current_preview(self),history_delivery=delivery_metadata(self.history),retained_reply=self.retained.metadata(),
                    action_outcomes=[dict(goal_id=r['goal_id'],action=r['action'],state=r['state']) for r in rows],
                    policy='Earlier effects are not undone by interruption. Uncertain effects require readback, never replay. Delivery metadata is not conversational text and must never be quoted as a reply. The latest user request determines whether to continue earlier reading, replace it, or cancel it. Use resume_previous_reply only when the user requests continuation; finish verified music controls first. Never repeat completed effects to resume reading.')

class EventBridge:
    def __init__(self,events,owner):self.events=events;self.owner=owner
    def emit(self,event,**fields):
        if 'generation' in fields:fields={**fields,'broker_generation':fields['generation'],'generation':self.owner.active_generation}
        self.events.emit(event,**fields)

class Conversation:
    def __init__(self,root,registry,transport,budget,events,context,*,engine=None,voice=False):
        from onit_candidate.engine import Engine
        self.root=Path(root);self.context=context;self.controller=None;self.events=events
        registry.productivity_records=context.productivity_records
        self._pending_capture=None;self._pending_capture_ids=[];self._preview_turn=None
        self.active_generation=None;self.voice=voice;self.wait_credited=0
        self.early_delivery=False;self.early_delivery_clear=None;self._early_delivery=None;self._pending_action_turn=None
        self.failed=asyncio.Event();self.request_voice_session_stop=None;self.end_after_reply_generation=None
        self.engine=engine or Engine(root,registry,transport,budget,EventBridge(events,self),enable_actions=True,ledger_path=self.root/'data/onit-action-intents.jsonl')
        if engine is None:self.engine.task_acknowledgments=bool(voice)
        self.engine.owner_current=lambda:self.current(self.active_generation)
        self.engine.before_reasoning=self.before_reasoning
        self.engine.before_budget_wait=self.credit_wait
        transport.before_capacity_wait=self.credit_wait
        from me3_assistant.hosted_search import bind_conversation
        bind_conversation(self)
        if self.engine.gateway and getattr(registry,'session_grant',{}).get('workspace_access') is True:
            self.engine.gateway.artifact_preview_offer=self.offer_artifact_preview
            self.engine.gateway.runtime_preview_offer=self.offer_runtime_preview
            self.engine.gateway.preview_accept=self.accept_pending_preview
            self.engine.gateway.preview_accept_declaration=self.preview_accept_declaration
            self.engine.gateway.preview_close_declaration=self.context.preview_close.declaration
            self.engine.gateway.close_preview=self.close_preview
            self.context.service_monitor.bind(self.root,self.runtime_delivery)
            self.engine.gateway.service_monitor=self.manage_service_monitor
    def configure_validated_delivery(self,*,enabled=False,clear_output=None):
        # Host-only opt-in. No model/tool field or environment enables this.
        if type(enabled) is not bool:raise CandidateError('validated_delivery_boolean_required')
        if enabled and (not self.voice or self.controller is None or not callable(clear_output)):raise CandidateError('validated_delivery_output_owner_required')
        self.early_delivery=enabled;self.early_delivery_clear=clear_output if enabled else None
    def consume_validated_speech(self,text,generation):
        sink=self._early_delivery
        return sink.consume(text,generation) if sink is not None else None
    def generation(self):return self.controller.clock.current if self.controller else 0
    def current(self,g):return g is not None and self.controller is not None and self.controller.current(g)
    def artifact_delivery(self):
        from onit_candidate.artifact_offer import guarded_delivery
        return guarded_delivery(self.root,self.engine.registry)
    def offer_artifact_preview(self,args,g):
        if not self.current(self.active_generation):raise asyncio.CancelledError()
        self.engine.gateway.check(g)
        delivery,source=self.artifact_delivery()
        if self.context.runtime_preview.pending:raise CandidateError("another_preview_pending")
        return self.context.artifact_preview.offer(args,delivery,source)
    def runtime_delivery(self):
        from onit_candidate.runtime_view import guarded_runtime
        return guarded_runtime(self.root,self.engine.registry)
    async def offer_runtime_preview(self,args,g):
        if not self.current(self.active_generation):raise asyncio.CancelledError()
        self.engine.gateway.check(g)
        if self.context.artifact_preview.pending:raise CandidateError('another_preview_pending')
        runtime,source=self.runtime_delivery()
        result=await self.context.runtime_preview.offer(args,runtime,source)
        self.engine.gateway.check(g)
        return result
    async def manage_service_monitor(self,args,g):
        owner=self.active_generation;identity=(self.context.conversation_id,self.context.revision)
        def current():return self.current(owner) and self.active_generation==owner and identity==(self.context.conversation_id,self.context.revision)
        if not current():raise asyncio.CancelledError()
        self.engine.gateway.check(g)
        if self.context.artifact_preview.pending or self.context.runtime_preview.pending:raise CandidateError('service_monitor_waits_for_preview_resolution')
        return await self.context.service_monitor.dispatch(args,current)
    async def close_preview(self,args,g):
        owner=self.active_generation
        if not self.current(owner):raise asyncio.CancelledError()
        self.engine.gateway.check(g)
        _,source=self.artifact_delivery()
        return await self.context.preview_close.request(args,source,lambda:self.active_generation==owner and self.current(owner))
    def preview_accept_declaration(self):
        turn=getattr(self,'_preview_turn',None)
        return turn.declaration() if turn is not None and turn.current() and not turn.used else None
    async def accept_pending_preview(self,args,g):
        turn=getattr(self,'_preview_turn',None)
        if turn is None:raise CandidateError('current_preview_acceptance_not_available')
        return await turn.accept(args,g)
    def contextual_preview_turn(self,owner,kind,raw,history,g,captured):
        from onit_candidate.artifact_offer import PreviewTurn
        pending=owner.pending
        if pending['context']!=(self.context.conversation_id,self.context.revision) or owner.clock()>=pending['expires']:
            owner.pending=None;owner.event=None;return 'That preview offer expired or the conversation changed. Nothing was opened.'
        if captured or len(history)<3 or not prompt_delivered(history,owner.pending['prompt']) or history[-2].get('partial_output'):
            return owner.pending['prompt']
        if any(c in raw for c in ('?','¿','？')) or normalized(raw) in CANCEL:
            owner.pending=None;owner.event=None;return None
        self._preview_turn=PreviewTurn(self,owner,kind,raw,history,g,captured)
        return None
    @property
    def pending(self):
        action=self.engine.gateway.actions.pending if self.engine.gateway else None
        return action if action is not None else self.context.artifact_preview.pending or self.context.runtime_preview.pending or self.context.service_monitor.pending
    @pending.setter
    def pending(self,value):
        if value is not None:raise CandidateError('pending_owned_by_guarded_gateway')
        self.context.artifact_preview.clear()
        self.context.runtime_preview.clear()
        self.context.service_monitor.pending=None
        if self.engine.gateway:self.engine.gateway.cancel_pending()
    def bind_pending_review_capture(self,generation,data,review_identity):
        # Denial-only host stamp: capture during an unfinished review can never
        # become confirmation merely because old speech finishes before ASR.
        p=self.pending
        identity=(bool(p),p.get('operation_id'),p.get('expected_digest')) if isinstance(p,dict) else (bool(p),None,None)
        request_id=data.get('request_id');text=data.get('text')
        if (not self.current(generation) or review_identity!=identity or not identity[0]
            or data.get('generation')!=generation or not isinstance(request_id,str) or not 1<=len(request_id)<=128
            or not isinstance(text,str) or not text.strip() or request_id in self._pending_capture_ids):
            raise CandidateError('exact_current_pending_capture_required')
        self._pending_capture_ids=(self._pending_capture_ids+[request_id])[-32:]
        self._pending_capture=(generation,identity,hashlib.sha256(text.strip().encode()).hexdigest())
    def consume_pending_review_capture(self,generation,raw):
        capture=self._pending_capture;self._pending_capture=None
        if capture is None:return False
        p=self.pending
        identity=(bool(p),p.get('operation_id'),p.get('expected_digest')) if isinstance(p,dict) else (bool(p),None,None)
        if capture!=(generation,identity,hashlib.sha256(raw.strip().encode()).hexdigest()):
            raise CandidateError('pending_capture_binding_changed')
        return True
    def interrupt(self):
        self._pending_capture=None
        self.context.artifact_preview.revoke_open()
        self.context.runtime_preview.revoke_open()
        self.end_after_reply_generation=None
        self.context.interrupt()
        if self.engine.task is not None:self.engine.invalidate()
    def abandon_goal(self):
        self.end_after_reply_generation=None
        self.pending=None;self.context.retained.clear();self.context.clarification=None;self.context.state='canceled'
    def complete_reply(self,generation):
        # Native answer generation is not completion of a voice goal. Keep its
        # committed-effect identity until the current full reply really plays.
        if self.current(generation) and self.active_generation==generation and self.context.state=='responding':
            self.context.state='completed'
            self.events.emit('assistant_goal_progress',generation=generation,goal_id=self.context.goal_id,state='completed')
        if self.end_after_reply_generation!=generation or not self.current(generation):return False
        self.end_after_reply_generation=None
        if not self.voice or not callable(self.request_voice_session_stop):return False
        self.pending=None;self.context.retained.clear();self.context.state='closed'
        self.events.emit('onit_session_end_after_reply',generation=generation,external_actions_undone=False,
                         software_reply_completed=True,acoustic_completion_measured=False)
        return self.request_voice_session_stop(generation)
    def fail_session_end(self,generation):
        # A failed/canceled goodbye must not leave this same owner Listening.
        # Interruption clears the flag; a stale finalizer cannot close a new turn.
        if (self.end_after_reply_generation!=generation or self.active_generation!=generation
            or not self.current(generation) or not self.voice or not callable(self.request_voice_session_stop)):return False
        self.end_after_reply_generation=None;self.pending=None;self.context.retained.clear();self.context.state='closed'
        self.events.emit('onit_session_end_without_complete_reply',generation=generation,
                         software_reply_completed=False,acoustic_completion_measured=False,
                         external_actions_undone=False,automatic_retry=False)
        return self.request_voice_session_stop(generation)
    def credit_wait(self,seconds):
        from me3_assistant.admission import AdmissionDenied
        if not self.current(self.active_generation):raise asyncio.CancelledError()
        ceiling=getattr(self.controller,'capacity_wait_ceiling',60) if self.voice else min(3600,self.engine.registry.config['runtime'].get('max_session_seconds',60))
        if not 0<seconds<=60 or self.wait_credited+seconds>ceiling:raise AdmissionDenied('capacity_wait_budget')
        if self.voice:self.controller.credit_provider_wait(self.active_generation,seconds)
        self.wait_credited+=seconds
    def before_reasoning(self):
        if not self.current(self.active_generation):raise asyncio.CancelledError()
        if self.voice and self.engine.budget.remaining('tts')<1:
            from speech_mcp.client import ProviderLimited
            self.engine.budget.exhausted.set();raise ProviderLimited('local_speech_allowance_exhausted_before_reasoning')
    def speech_segments_for(self,text,generation):
        if not self.current(generation) or self.active_generation!=generation or self.pending:return None
        result=self.context.last_result
        if not isinstance(result,dict) or result.get('answer')!=text or 'speech_segments' not in result:return None
        if result.get('status') in {'waiting_confirmation','verbatim_passage'}:return None
        from onit_candidate.speech_presentation import validate_segments
        rows=validate_segments(result['speech_segments'],text=text)
        if result.get('speech_projection') not in {'source','spoken'}:raise CandidateError('invalid_host_speech_projection')
        return dict(segments=rows,projected=result['speech_projection']=='spoken')
    async def answer(self,history):
        g=self.generation()
        previous=self.engine.task
        if previous is not None and previous is not asyncio.current_task():
            # Never start a replacement before the canceled child has exited.
            async with asyncio.timeout(5):await asyncio.shield(asyncio.gather(previous,return_exceptions=True))
        if not self.current(g):raise asyncio.CancelledError()
        old_turn=getattr(self,'_pending_action_turn',None)
        if old_turn is not None:old_turn.close()
        self._pending_action_turn=None
        self.active_generation=g;self.wait_credited=0;self._early_delivery=None;self._preview_turn=None
        if not history or history[-1].get('type')!='user_input':raise CandidateError('direct_user_turn_required')
        raw=latest_text(history);surface=normalized(raw)
        captured_during_review=self.consume_pending_review_capture(g,raw)
        if self.voice and callable(self.request_voice_session_stop):
            ending=candidate_session_end_kind(raw)
            if ending:
                self.abandon_goal()
                if ending=='immediate':
                    self.request_voice_session_stop(g);raise asyncio.CancelledError()
                # A direct natural farewell uses the existing speech owner,
                # then generation-bound closure. It is not a new model goal.
                reply='Goodbye! Take care.'
                self.end_after_reply_generation=g
                self.context.last_result=dict(answer=reply,status='session_ending',model_calls=0,end_session_after_reply=True)
                self.events.emit('onit_direct_farewell_ready',generation=g,reasoning_requests=0,
                                 speech_path='existing TTS and paced playout',acoustic_completion_measured=False)
                return reply
        monitor=self.context.service_monitor
        if monitor.pending is not None:
            p=monitor.pending
            if self.engine.gateway and self.engine.gateway.actions.pending:raise CandidateError('monitor_cannot_confirm_another_action')
            if p['context']!=(self.context.conversation_id,self.context.revision) or monitor.clock()>=p['expires']:
                monitor.pending=None;return 'That monitoring review expired. No watch was started.'
            intent=confirmation_intent(raw)
            if captured_during_review or intent=='question' or not prompt_delivered(history,p['prompt']) or history[-2].get('partial_output'):return p['prompt']
            if intent=='affirmative':
                identity=(self.context.conversation_id,self.context.revision)
                result=await monitor.confirm(lambda:self.current(g) and identity==(self.context.conversation_id,self.context.revision))
                self.context.last_result=result
                return 'The bounded watch has started. Any notification will appear in the GUI.'
            monitor.pending=None
            if surface in CANCEL:return 'The unconfirmed watch is canceled. No watch was started.'
        runtime_preview=self.context.runtime_preview
        if runtime_preview.pending is not None:
            if self.engine.gateway and self.engine.gateway.actions.pending:
                runtime_preview.clear();raise CandidateError('runtime_preview_cannot_confirm_another_action')
            if confirmation_intent(raw) in {'affirmative','question'}:
                reply=await runtime_preview.respond(raw,history,captured_during_review=captured_during_review,runtime_factory=self.runtime_delivery)
                self.context.state='waiting_confirmation' if runtime_preview.pending else 'responding' if self.voice else 'completed'
                self.context.last_result=dict(answer=reply,status='runtime_preview_confirmation',model_calls=0,functionality_verified=False)
                return reply
            contextual=self.contextual_preview_turn(runtime_preview,'runtime',raw,history,g,captured_during_review)
            if contextual is not None:return contextual
        elif runtime_preview.event is not None:runtime_preview.revoke_open()
        preview=self.context.artifact_preview
        if preview.pending is not None:
            if self.engine.gateway and self.engine.gateway.actions.pending:
                preview.clear();raise CandidateError('artifact_preview_cannot_confirm_another_action')
            if confirmation_intent(raw) in {'affirmative','question'}:
                reply=preview.respond(raw,history,captured_during_review=captured_during_review,delivery_factory=self.artifact_delivery)
                self.context.state='waiting_confirmation' if preview.pending else 'responding' if self.voice else 'completed'
                self.context.last_result=dict(answer=reply,status='artifact_preview_confirmation',model_calls=0,functionality_verified=False)
                return reply
            contextual=self.contextual_preview_turn(preview,'static',raw,history,g,captured_during_review)
            if contextual is not None:return contextual
        elif preview.event is not None:
            preview.clear()
        if self.pending and confirmation_intent(raw)!='none':
            # Questions, interrupted prompts and model/tool text cannot authorize.
            if confirmation_intent(raw)=='affirmative' and (captured_during_review or not prompt_delivered(history,self.pending['prompt']) or history[-2].get('partial_output')):
                reply='The review is not complete yet, so I have not acted on that confirmation. Ask me to repeat it, or cancel.'
                self.context.state='waiting_confirmation'
                self.context.last_result=dict(answer=reply,status='waiting_confirmation',review_complete=False,execution_started=False,model_calls=0)
                return reply
            if captured_during_review or confirmation_intent(raw)=='question' or not prompt_delivered(history,self.pending['prompt']) or history[-2].get('partial_output'):return self.pending['prompt']
            result=await self.engine.confirm_action(history)
            self.context.state='waiting_confirmation' if self.pending else 'responding' if self.voice else 'completed'
            self.context.last_result=result;return result['reply']
        if surface in CANCEL|{'stop','stop talking','stop generating','stop responding','be quiet'}:
            had_pending=bool(self.pending);self.abandon_goal()
            return 'Canceled the unconfirmed proposal.' if had_pending else 'Stopped. Previously completed actions were not undone.'
        action_pending=self.engine.gateway.actions.pending if self.engine.gateway else None
        if action_pending is not None and confirmation_intent(raw)=='none':
            if captured_during_review or not prompt_delivered(history,action_pending['prompt']) or history[-2].get('partial_output'):return action_pending['prompt']
            if self.engine.gateway.actions.clock()>=action_pending['expires']:
                self.engine.gateway.cancel_pending();return 'That proposal expired. Please make a fresh request for a new review.'
            from onit_candidate.pending_confirmation import PendingActionTurn,ReviewExpired
            try:self._pending_action_turn=PendingActionTurn(self,raw,history,g,captured_during_review)
            except ReviewExpired as expired:
                try:return expired.turn.expire()['answer']
                finally:expired.turn.close()
        semantic=self._pending_action_turn
        preserve_review=bool(self.pending) or getattr(self,'_preview_turn',None) is not None or semantic is not None
        try:
            goal=self.context.begin();attempt=uuid.uuid4().hex
            if getattr(self,'_preview_turn',None) is not None:self._preview_turn.bind()
            self.events.emit('assistant_goal_progress',generation=g,goal_id=goal,state='started')
            delivery=None
            # Unresolved effects prohibit speculative delivery, not ordinary
            # conversation/read-only status. Keep the full-answer path and all
            # Gateway replay/effect and sink ownership guards unchanged.
            if semantic is None and self.early_delivery and self.voice and not preserve_review and not (self.engine.gateway and self.engine.gateway.blockers()):
                from onit_candidate.validated_speech import CandidateDeliverySink
                delivery=CandidateDeliverySink(self,self.controller,g,goal,self.early_delivery_clear)
            self._early_delivery=delivery
        except BaseException:
            if semantic is not None:semantic.close()
            self._pending_action_turn=None
            raise

        try:
            async with AsyncExitStack() as scopes:
                planning=getattr(self.controller,'planning_scope',None)
                if self.voice and callable(planning):
                    runtime=getattr(getattr(self.engine,'registry',None),'config',{}).get('runtime',{})
                    await scopes.enter_async_context(planning(g,min(3600,runtime.get('max_session_seconds',300))))
                acknowledgment=getattr(self,'planning_ack_scope',None)
                if self.voice and callable(acknowledgment):await scopes.enter_async_context(acknowledgment(g,goal))
                semantic=self._pending_action_turn
                operation=self.engine.answer(raw,self.root/'.tmp'/('onit-voice-'+attempt),
                           self.events.path.parent/('onit-turn-'+attempt),history=None if semantic is not None else onit_history(history[:-1]),
                           goal_id=goal,context_state=self.context.metadata(self.engine.gateway),lazy_tools=False if semantic is not None else getattr(self.engine,'uses_lean_tools',False),retained_reply=None if semantic is not None else self.context.retained.snapshot(),session_end_allowed=semantic is None and self.voice and callable(self.request_voice_session_stop),reading_controls_allowed=semantic is None,preserve_review=preserve_review,spoken_response=self.voice and semantic is None,**(dict(pending_review_turn=semantic) if semantic is not None else {}),**(dict(early_delivery=True,delivery_sink=delivery) if delivery is not None else {}),**(dict(speech_languages=getattr(self.controller.real_clients,'speech_languages',('en',))) if self.voice and self.controller is not None else {}))
                result=await delivery.drive(operation) if delivery is not None else await operation
            if not self.current(g):raise asyncio.CancelledError()
            if self._pending_action_turn is not None:
                turn=self._pending_action_turn;result=await turn.finish(result)
                if result.get('status')=='pending_review_revision':
                    retired=turn.begin_revision()
                    if retired is not None:result=retired
                    else:
                        decision_calls=result['model_calls']
                        result=await self.engine.answer(raw,self.root/'.tmp'/('onit-revision-'+attempt),
                            self.events.path.parent/('onit-revision-'+attempt),history=onit_history(history[:-1]),
                            goal_id=goal,context_state=self.context.metadata(self.engine.gateway),lazy_tools=False,
                            retained_reply=None,session_end_allowed=False,reading_controls_allowed=False,preserve_review=False,
                            spoken_response=False,pending_review_revision=turn)
                        result=turn.finish_revision(result)
                        result=dict(result,model_calls=decision_calls+result['model_calls'])
            if self.pending and result.get('status')!='waiting_confirmation':
                # A model paraphrase cannot replace the immutable host review.
                result={**result,'answer':self.pending['prompt'],'status':'waiting_confirmation'}
            if result.get('status') in {'clarification_required','needs_details'}:
                self.context.clarification=copy.deepcopy(result.get('pending_intent'));self.context.state='awaiting_details'
            else:
                self.context.clarification=None
                self.context.state='waiting_confirmation' if result.get('status')=='waiting_confirmation' else 'responding' if self.voice else 'completed'
            self.context.last_result=result
            if result.get('end_session_after_reply'):
                if not self.voice or not callable(self.request_voice_session_stop):raise CandidateError('bound_voice_owner_required_for_session_end')
                self.end_after_reply_generation=g
            if delivery is None or not delivery.retained_registered:self.context.retained.remember(result,g)
            self.events.emit('assistant_goal_progress',generation=g,goal_id=goal,state=self.context.state)
            return result['answer']
        except asyncio.CancelledError:
            self.context.interrupt();self.events.emit('assistant_goal_progress',generation=g,goal_id=goal,state='canceled');raise
        except BaseException:
            self.context.state='failed';self.failed.set()
            self.events.emit('assistant_goal_progress',generation=g,goal_id=goal,state='failed');raise
        finally:
            turn=self._pending_action_turn
            if turn is not None:turn.close()
            self._pending_action_turn=None;self._preview_turn=None
    async def close(self):
        task=self.engine.task
        if task is not None:
            self.engine.invalidate();await asyncio.gather(task,return_exceptions=True)
        self.pending=None
        if self.context.state not in {'failed','completed'}:self.context.state='closed'

def controller_class():
    from onit_candidate.spoken import CandidateSpeech as AssistantController
    class SharedController(AssistantController):
        @property
        def history(self):return self.shared_context.history if hasattr(self,'shared_context') else self._history
        @history.setter
        def history(self,value):
            if hasattr(self,'shared_context'):self.shared_context.history=value
            else:self._history=value
        def note_reply_submission(self,generation,prefix):
            if self.current(generation):self.shared_context.retained.submitted(generation,prefix)
        async def speak(self,text,generation,style,**flags):
            if not flags.get('greeting') and not flags.get('error_notice'):
                consume=getattr(self.provider,'consume_validated_speech',None)
                streamed=consume(text,generation) if callable(consume) else None
                if streamed is not None:
                    try:return streamed
                    finally:
                        fail=getattr(self.provider,'fail_session_end',None)
                        if callable(fail):fail(generation)
            presentation=None
            select=getattr(self.provider,'speech_segments_for',None)
            if callable(select) and not flags.get('greeting') and not flags.get('error_notice'):presentation=select(text,generation)
            if presentation is not None:flags.update(speech_segments=presentation['segments'],speech_projected=presentation['projected'])
            try:
                played=await super().speak(text,generation,style,**flags)
                if played and self.current(generation) and hasattr(self,'shared_context') and not flags.get('greeting') and not flags.get('error_notice'):
                    from onit_candidate.presentation import spoken_text
                    if presentation is not None:
                        from onit_candidate.speech_presentation import project
                        submitted=''.join(row['text'] for row in project(presentation['segments'],already_projected=presentation['projected']))
                    else:submitted=spoken_text(text)
                    self.note_reply_submission(generation,submitted)
                    complete=getattr(self.provider,'complete_reply',None)
                    if callable(complete):complete(generation)
                return played
            finally:
                if not flags.get('greeting') and not flags.get('error_notice'):
                    fail=getattr(self.provider,'fail_session_end',None)
                    if callable(fail):fail(generation)
        def interrupt(self):
            self.provider.interrupt()
            return super().interrupt()
        async def typed_turn(self,text,generation):
            """Same controller/TTS/history owner, with zero ASR for typed input."""
            self.events.emit('core_turn_started',generation=generation,source='text',deadline_seconds=self.turn_deadline)
            try:
                async with asyncio.timeout(self.turn_deadline) as timer:
                    self.deadlines[generation]={'timer':timer,'audio_credited':False}
                    self.history=(self.history+[dict(type='user_input',content=[dict(type='text',text=text)])])[-16:]
                    self.pending_history_generation=generation
                    answer=await self.provider.answer(list(self.history))
                    if not self.current(generation):return
                    if await self.speak(answer,generation,'normal') and self.current(generation):
                        self.history.append(dict(type='model_output',content=[dict(type='text',text=answer)]));self.pending_history_generation=None
                    else:self.close_pending_history(generation,'failed')
            except asyncio.CancelledError:self.close_pending_history(generation,'cancelled');raise
            except Exception as exc:
                self.close_pending_history(generation,'failed')
                self.events.emit('turn_error',generation=generation,stage='typed_voice_turn',error_type=type(exc).__name__)
                if hasattr(self.clients,'mark_failure'):self.clients.mark_failure('reasoning',None)
            finally:self.deadlines.pop(generation,None);self.events.emit('core_turn_settled',generation=generation)
    return SharedController

async def submit_text(processor,text):
    from me3_completion.pipeline import Ownership
    if processor.closing or processor.controller.closed:raise CandidateError('voice_session_closing')
    if not getattr(processor,'candidate_listening',False):raise CandidateError('wait_for_listening')
    if processor.controller.turns>=processor.controller.max_turns:raise CandidateError('configured_turn_limit')
    processor.token+=1
    # Invalidate pending endpoint/overlap/cue jobs before adopting typed input.
    if getattr(processor,'floor_admission_enabled',False):
        token=processor.token
        generation=await processor.takeover('typed_user_request')
        if not await processor.retire_floor_handoff(token,generation):raise asyncio.CancelledError()
    else:
        jobs=[j for j in processor.jobs if j is not asyncio.current_task() and not j.done()]
        for job in jobs:job.cancel()
        if jobs:await asyncio.gather(*jobs,return_exceptions=True)
        generation=await processor.takeover('typed_user_request')
    processor.pcm.clear();processor.preroll.clear();processor.cued=False;processor.overflow=False
    processor.speaking=False;processor.starting=None;processor.onset=None;processor.active_end=0
    processor.controller.slot=None;processor.controller.saved=None;processor.controller.prefetch.ready.clear()
    processor.transition(Ownership.PROCESSING,'typed_user_request')
    processor.controller.turns+=1;processor.controller.spawn(processor.controller.typed_turn(text,generation))
