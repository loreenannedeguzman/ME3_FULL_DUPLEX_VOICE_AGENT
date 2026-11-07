"""Optional overlap admission. No ASR calls, actions or confirmation ownership."""
import asyncio
import time
from onit_candidate.floor import FloorError,FloorReviewChanged,choice,digest,observation,pending_fingerprint

QUEUED_INPUT_SECONDS=180

class FloorAdmission:
    def __init__(self,processor,provider,events,decider,*,response_state,processing_state):
        self.p=processor;self.provider=provider;self.events=events;self.decider=decider
        self.response_state=response_state;self.processing_state=processing_state
        self.held=None;self.timer=None;self.queued=None;self.queued_job=None
    def fresh(self,token,generation,task,fingerprint):
        p=self.p
        return bool(p.valid(token) and p.controller.clock.current==generation and p.floor_response_matches(task,generation) and not getattr(task,'done',lambda:False)() and pending_fingerprint(self.provider)==fingerprint)
    def advisory_current(self,token,generation,task,fingerprint):
        if self.fresh(token,generation,task,fingerprint):return True
        p=self.p;c=p.controller
        # STARTING is provisional capture, not a new floor owner. Keep only
        # this exact decision alive; strict fresh()/onset settlement still
        # prohibit admitting it while that onset is unresolved.
        return bool(not p.closing and not p.pipeline_failed.is_set() and not c.closed
            and token==p.token and not p.speaking and getattr(p.last_vad,'name',None)=='STARTING'
            and c.clock.current==generation and c.current(generation)
            and p.floor_response_matches(task,generation) and not task.done() and not task.cancelling()
            and pending_fingerprint(self.provider)==fingerprint)
    def cancel_timer(self):
        if self.timer is not None and self.timer is not asyncio.current_task():self.timer.cancel()
        self.timer=None
    def cancel_queued(self,reason):
        record=self.queued;self.queued=None
        job=self.queued_job;self.queued_job=None
        if job is not None and job is not asyncio.current_task() and not job.done():job.cancel()
        if record is not None:self.events.emit('floor_queued_input_discarded',generation=record['generation'],reason=reason,text_retained=False,automatic_retry=False)
    def queued_fresh(self,record):
        p=self.p;c=p.controller;context=getattr(self.provider,'context',None)
        lane=getattr(p,'warm_floor_lane',None)
        return bool(self.queued is record and not p.closing and not p.pipeline_failed.is_set()
            and not c.closed and c.clock.current==record['generation']
            and c.current(record['generation']) and c.task is record['task']
            and pending_fingerprint(self.provider) is None
            and context is not None and (context.conversation_id,context.revision,context.goal_id)==record['context']
            and lane is not None and lane.source()==record['source']==lane.expected_source)
    def queue_input(self,token,pcm,generation,task,data):
        if self.queued is not None:
            # A later ACK or another proposed queued request cannot overwrite
            # the already admitted input. It owns no new task or action.
            self.events.emit('floor_queue_full',generation=generation,accepted=False,original_queued_input_preserved=True,text_retained=False)
            self.p.pcm.clear();self.p.preroll.clear();self.p.probing=False;self.p.gate.resume()
            self.p.transition(self.response_state,'floor_queue_full_original_work_continues')
            lane=getattr(self.p,'warm_floor_lane',None)
            if lane is not None:lane.resumed()
            return
        context=self.provider.context;lane=getattr(self.p,'warm_floor_lane',None)
        maximum=getattr(self.p.controller,'max_pcm_bytes',480000)
        if lane is None or type(maximum) is not int or not 3200<=maximum<=120*32000:raise FloorError('floor_queued_capture_bound')
        if not isinstance(pcm,bytes) or len(pcm)>maximum:
            self.events.emit('floor_queue_capture_rejected',generation=generation,accepted=False,original_work_canceled=False,text_retained=False)
            self.p.pcm.clear();self.p.preroll.clear();self.p.probing=False;self.p.gate.resume();lane.resumed();return
        record=dict(token=token,generation=generation,task=task,data=dict(data),pcm=pcm,
            context=(context.conversation_id,context.revision,context.goal_id),source=lane.source())
        self.queued=record
        self.queued_job=self.p.spawn(self.drain_queued(record))
        self.p.pcm.clear();self.p.preroll.clear();self.p.probing=False;self.p.gate.resume()
        self.p.transition(self.response_state,'floor_followup_queued_original_work_continues')
        self.events.emit('floor_input_queued',generation=generation,one_input=True,serial_not_parallel=True,action_authority=False,text_retained=False)
        lane.resumed()
    async def drain_queued(self,record):
        try:
            done,pending=await asyncio.wait({record['task']},timeout=QUEUED_INPUT_SECONDS)
            if pending:
                self.events.emit('floor_queued_input_discarded',generation=record['generation'],reason='bounded_wait_expired',automatic_retry=False,text_retained=False);return
            task=record['task']
            if task.cancelled() or task.exception() is not None or not self.queued_fresh(record):return
            if self.provider.context.state!='completed':return
            token=self.p.token
            if self.p.probing or self.p.pcm or not self.p.valid(token):return
            new=await self.p.takeover('queued_independent_followup_after_completion')
            if new!=record['generation']+1:raise FloorError('floor_queued_generation_changed')
            if not await self.p.retire_floor_handoff(token,new):return
            if (self.p.closing or not self.p.valid(token) or pending_fingerprint(self.provider) is not None
                or self.provider.context.conversation_id!=record['context'][0]
                or self.provider.context.revision!=record['context'][1]):return
            c=self.p.controller;c.slot=None;c.saved=None;c.prefetch.ready.clear()
            self.p.transition(self.processing_state,'adopt_queued_exact_asr_after_completion')
            self.events.emit('floor_queued_input_admitted',generation=new,duplicate_ASR=False,original_work_replayed=False,text_retained=False)
            c.submit_recognized(record['pcm'],new,record['data'])
        finally:
            if self.queued is record:self.queued=None
            if self.queued_job is asyncio.current_task():self.queued_job=None
    def context_text(self,kind,maximum):
        # No raw pending review is copied into the floor classifier context.
        if getattr(self.provider,'pending',None) is not None:return ''
        rows=getattr(getattr(self.provider,'context',None),'history',[])
        for row in reversed(rows):
            if row.get('type')==kind and not row.get('control_state'):
                text=' '.join(p['text'] for p in row.get('content',[]) if p.get('type')=='text' and isinstance(p.get('text'),str))
                return text[-maximum:]
        return ''
    def current_generated_text(self,generation,maximum=1200):
        # Generated content is not a claim that any prefix was acoustically heard.
        # Completed history may still contain only the preceding answer here.
        if getattr(self.provider,'pending',None) is not None:return ''
        context=getattr(self.provider,'context',None)
        if context is None or getattr(context,'state',None)!='responding' or getattr(self.provider,'active_generation',None)!=generation:return ''
        retained=getattr(getattr(context,'retained',None),'entries',[])
        for entry in reversed(retained):
            if type(entry.get('generation')) is int and entry['generation']==generation:
                text=entry.get('text')
                return text[-maximum:] if isinstance(text,str) else ''
        result=getattr(context,'last_result',None)
        if not isinstance(result,dict):return ''
        if result.get('status') in {'waiting_confirmation','reading_held'}:return ''
        text=result.get('answer',result.get('reply'))
        return text[-maximum:] if isinstance(text,str) else ''
    async def clarify(self,token,generation,task,fingerprint,reason,*,message=None):
        if not self.fresh(token,generation,task,fingerprint):return
        self.cancel_timer();self.held=None
        new=await self.p.takeover(reason)
        if new!=generation+1 or not self.p.controller.current(new) or not self.p.valid(token) or pending_fingerprint(self.provider)!=fingerprint:return
        self.p.pcm.clear();self.p.preroll.clear();self.p.controller.turns+=1
        self.events.emit('floor_clarification',generation=new,action_authority=False,text_retained=False)
        self.p.controller.spawn(self.p.controller.speak(message or 'Were you speaking to me? Please say your complete request.',new,'normal',error_notice=True))
    async def expire(self,record,token,generation,task,fingerprint):
        await asyncio.sleep(6)
        if self.held is record and self.fresh(token,generation,task,fingerprint):
            await self.clarify(token,generation,task,fingerprint,'floor_hold_expired_no_execution')
    def resume_new_review(self,token,generation,task,record,context):
        p=self.p;c=p.controller;owner=self.provider;now=owner.context;lane=getattr(p,'warm_floor_lane',None)
        pending=owner.pending
        if (not isinstance(pending,dict) or not pending or p.closing or p.pipeline_failed.is_set()
            or c.closed or not p.valid(token) or c.clock.current!=generation or not c.current(generation)
            or c.task is not task or task.done() or task.cancelling() or p.floor_response is not record
            or not p.floor_response_matches(task,generation) or owner.active_generation!=generation
            or (now.conversation_id,now.revision,now.goal_id)!=context
            or lane is None or lane.source()!=lane.expected_source):return False
        gateway=getattr(owner.engine,'gateway',None)
        if gateway is not None and pending is gateway.actions.pending:
            if gateway.goal!=now.goal_id or not gateway.pending_key or gateway.pending_key[0]!=now.goal_id:return False
        elif not any(pending is getattr(now,name).pending and pending.get('context')==(now.conversation_id,now.revision)
            for name in ('artifact_preview','runtime_preview','service_monitor')):return False
        self.cancel_timer();self.held=None;self.cancel_queued('new_review')
        p.pcm.clear();p.preroll.clear();p.probing=False;p.gate.resume();p.transition(self.response_state,'floor_pre_review_capture_discarded')
        self.events.emit('floor_capture_discarded_new_review',generation=generation,confirmation_eligible=False,duplicate_ASR=False,text_retained=False)
        lane.resumed();return True
    async def discard_pre_review_capture(self,token,generation,task,record,context):
        lane=getattr(self.p,'warm_floor_lane',None)
        if lane is None:return False
        # Even a review arriving before advisory dispatch must retire its unused
        # READY/preparing child before the original review can resume.
        lane.invalidate()
        if lane.retirement is not None:
            async with asyncio.timeout(5):await asyncio.shield(lane.retirement)
        return self.resume_new_review(token,generation,task,record,context)
    async def admit(self,token,pcm,duration,generation,task,data,*,review_identity=None):
        fingerprint=pending_fingerprint(self.provider)
        if not self.fresh(token,generation,task,fingerprint):
            if self.p.valid(token) and self.p.controller.clock.current==generation:raise FloorError('floor_response_owner_lost')
            return
        if (not isinstance(data,dict) or data.get('generation')!=generation+1
            or not isinstance(data.get('request_id'),str) or not 1<=len(data['request_id'])<=128
            or type(data.get('generation')) is not int
            or not isinstance(data.get('text'),str) or not data['text'].strip() or len(data['text'])>4000):
            raise FloorError('floor_exact_asr_identity_required')
        if fingerprint is not None:
            if review_identity is None or review_identity!=self.p.overlap_review_identity():
                raise FloorError('floor_capture_review_identity_required')
            bind=getattr(self.provider,'bind_pending_review_capture',None)
            if not callable(bind):raise FloorError('floor_pending_host_capture_guard_required')
            self.cancel_timer();self.held=None
            lane=getattr(self.p,'warm_floor_lane',None)
            if lane is not None:lane.invalidate()
            new=await self.p.takeover('floor_pending_review_exact_input')
            # Retire old synthesis and provider leases before adopting one
            # unchanged ASR result. No classifier or new ASR is involved.
            async with asyncio.timeout(5):
                done,pending=await asyncio.wait({task},timeout=5)
                if pending:raise FloorError('floor_pending_response_cleanup_unsettled')
                if not task.cancelled() and task.exception() is not None:raise FloorError('floor_pending_response_cleanup_failed')
                retired=await self.p.retire_floor_handoff(token,new)
            if (not retired or new!=generation+1 or not self.p.controller.current(new)
                or not self.p.valid(token) or pending_fingerprint(self.provider)!=fingerprint
                or review_identity!=self.p.overlap_review_identity()):return
            bind(new,data,review_identity)
            self.p.pcm.clear();self.p.preroll.clear()
            self.p.controller.slot=None;self.p.controller.saved=None;self.p.controller.prefetch.ready.clear()
            self.p.transition(self.processing_state,'adopt_pending_exact_asr_host_guard')
            self.events.emit('floor_pending_review_handoff',generation=new,confirmation_eligible=False,duplicate_ASR=False,text_retained=False)
            self.p.controller.submit_recognized(pcm,new,data);return
        held=self.held['text'] if self.held and self.held['generation']==generation else ''
        self.cancel_timer()
        capture=digest(dict(generation=generation,token=token,asr_request_id=data['request_id']))[:32]
        value=observation(dict(capture_id=capture,generation=generation,token=token,transcript=data.get('text'),held_text=held,current_generated_assistant_text=self.current_generated_text(generation),previous_owner_text=self.context_text('user_input',800),pending_review=fingerprint is not None,pending_fingerprint=fingerprint))
        binding=digest(value);start=time.monotonic()
        def current():
            if fingerprint is None and pending_fingerprint(self.provider) is not None:
                raise FloorReviewChanged('floor_capture_predates_new_review')
            return self.advisory_current(token,generation,task,fingerprint)
        review_identity=self.p.overlap_review_identity()
        context=getattr(self.provider,'context',None)
        context_binding=(context.conversation_id,context.revision,context.goal_id)
        record=self.p.floor_response
        try:result=await self.decider(value,current=current)
        except FloorReviewChanged:
            # The worker/transport has settled before this typed signal escapes
            # WarmFloorLane. This old capture never becomes review authority.
            if fingerprint is None and await self.discard_pre_review_capture(token,generation,task,record,context_binding):return
            raise
        except FloorError as error:
            from me3_assistant.warm_floor import WarmFloorUnavailable
            if getattr(self.p,'warm_floor_lane',None) is None or not isinstance(error,WarmFloorUnavailable):raise
            await self.clarify(token,generation,task,fingerprint,'floor_worker_unavailable_no_execution',message='I could not safely process that interruption. Please say your complete request.');return
        try:
            async with asyncio.timeout(5):
                settled=await self.p.wait_overlap_onset_settled(token,generation,review_identity)
        except RuntimeError as error:
            if (str(error) in {'overlap_review_changed_during_recognition','overlap_review_changed_during_onset_settlement'}
                and fingerprint is None and pending_fingerprint(self.provider) is not None
                and await self.discard_pre_review_capture(token,generation,task,record,context_binding)):return
            raise
        if fingerprint is None and pending_fingerprint(self.provider) is not None:
            if await self.discard_pre_review_capture(token,generation,task,record,context_binding):return
            raise FloorReviewChanged('floor_new_review_owner_changed')
        if not settled or not self.fresh(token,generation,task,fingerprint):
            if self.p.valid(token) and self.p.controller.clock.current==generation:raise FloorError('floor_response_owner_lost')
            self.events.emit('floor_admission_stale' ,generation=generation,token=token,text_retained=False);return
        result=choice(result,binding,pending_review=value['pending_review']);disposition=result['disposition']
        self.events.emit('floor_disposition',generation=generation,token=token,disposition=disposition,elapsed_seconds=round(time.monotonic()-start,3),action_authority=False,text_retained=False)
        if disposition in {'ignore','ack_resume'}:
            if held:
                # Background speech does not revoke an unfinished owner turn.
                self.timer=self.p.spawn(self.expire(self.held,token,generation,task,fingerprint));self.p.pcm.clear();self.p.preroll.clear()
                lane=getattr(self.p,'warm_floor_lane',None)
                if lane is not None:lane.retained_turn()
                return
            self.held=None;self.p.pcm.clear();self.p.preroll.clear();self.p.probing=False
            if disposition=='ack_resume':self.p.controller.listener_ack_count+=1
            self.p.gate.resume();self.p.transition(self.response_state,'floor_'+disposition+'_same_response')
            lane=getattr(self.p,'warm_floor_lane',None)
            if lane is not None:lane.resumed()
            return
        if disposition=='hold':
            record=dict(text=(held+' '+data['text']).strip(),generation=generation)
            self.held=record;self.p.pcm.clear();self.p.preroll.clear();self.p.probing=False
            self.timer=self.p.spawn(self.expire(record,token,generation,task,fingerprint))
            lane=getattr(self.p,'warm_floor_lane',None)
            if lane is not None:lane.retained_turn()
            return
        if disposition=='queue':
            self.held=None;self.cancel_timer()
            self.queue_input(token,pcm,generation,task,dict(data,text=(held+' '+data['text']).strip()))
            return
        if disposition=='clarify':
            await self.clarify(token,generation,task,fingerprint,'floor_directedness_unclear_no_execution');return
        # Only an accepted, still-current turn reaches existing command handling.
        # This edge supplies NO confirmation or action authorization itself.
        self.held=None;self.cancel_timer();adopted=dict(data,text=(held+' '+data['text']).strip())
        new=await self.p.takeover('floor_accepted_directed_turn')
        if getattr(self.p,'warm_floor_lane',None) is not None:
            async with asyncio.timeout(5):
                done,pending=await asyncio.wait({task},timeout=5)
                if pending:raise FloorError('warm_floor_old_response_cleanup_unsettled')
                if not task.cancelled() and task.exception() is not None:raise FloorError('warm_floor_old_response_cleanup_failed')
                if not await self.p.retire_floor_handoff(token,new):return
        if new!=generation+1 or not self.p.controller.current(new) or not self.p.valid(token) or pending_fingerprint(self.provider)!=fingerprint:return
        self.p.pcm.clear();self.p.preroll.clear();self.p.transition(self.processing_state,'adopt_floor_validated_existing_asr')
        self.p.controller.submit_recognized(pcm,new,adopted)
