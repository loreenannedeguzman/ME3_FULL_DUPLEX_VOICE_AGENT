"""Inactive controller bridge for host-issued native delivery commits.

Real playout stays on controller.task. A new producer task may drain Onit while
the existing playback gate pauses/resumes that same owner. No model dictionary
can authorize this bridge. Host clear_output must verify current/retired output.
"""
import asyncio,copy
from onit_candidate.protocol import CandidateError
from onit_candidate.validated_delivery import UnitCommit,binding as checked_binding,digest
from onit_candidate.validated_speech_queue import Binding,Unit,FullComplete,QueuedDelivery
from onit_candidate.speech_presentation import validate_segments,project
from onit_candidate.presentation import speech_parts

class CandidateDeliverySink:
    def __init__(self,provider,controller,generation,goal,clear_output):
        if not callable(clear_output):raise CandidateError('validated_output_clear_hook_required')
        self.provider=provider;self.controller=controller;self.owner_generation=generation;self.goal=goal;self.clear_output=clear_output
        self.identity=None;self.issuer=None;self.queue=None;self.originals=[];self.locals=[]
        self.ready=asyncio.Event();self.producer_done=asyncio.Event();self.task=None
        self.partial=None;self.terminal_kind=None;self.terminal_answer=None;self.terminal_segments=None
        self.outcome=None;self.consumed=False;self.retained_registered=False;self.synthesized=0;self.played=0
    def current(self,identity):
        if type(identity) is not dict or identity.get('goal_id')!=self.goal or identity.get('owner_generation')!=self.owner_generation:return False
        if self.identity is not None and identity!=self.identity:return False
        engine=self.provider.engine;gateway=engine.gateway
        # A successful Engine finally sets its stop event after issuing trusted
        # FULL_COMPLETE. That is native cleanup, not revocation of committed
        # speech. A new Engine generation or output/context owner still revokes.
        engine_owned=(getattr(engine,'generation',None)==identity.get('engine_generation') and
            (self.terminal_kind=='FULL_COMPLETE' or engine.current(identity.get('engine_generation'))))
        return bool(self.provider.current(self.owner_generation) and self.provider.active_generation==self.owner_generation
            and self.provider.context.goal_id==self.goal and engine_owned
            and (gateway is None or not gateway.actions.pending and not gateway.blockers()))
    def start(self,identity,issuer):
        identity=checked_binding(identity)
        if self.identity is not None or issuer is None or not self.current(identity):raise CandidateError('validated_sink_start_binding')
        self.identity=dict(identity);self.issuer=issuer
        register=getattr(getattr(self.clear_output,'__self__',None),'register',None)
        if callable(register) and register(self,self.identity) is not True:raise CandidateError('validated_output_registration_rejected')
        bound=Binding(**identity)
        def projection(text,language):
            rows=project([dict(language=language,text=text)],available=self.controller.real_clients.speech_languages)
            return ''.join(row['text'] for row in rows)
        async def synthesize(text,language,binding):
            return await self.controller.validated_synthesize(text,language,self.owner_generation)
        async def play(pcm,binding):
            await self.controller.validated_play(pcm,self.owner_generation,self.played)
            self.played+=1
        async def clear(binding):return await self.clear_output(dict(self.identity))
        self.queue=QueuedDelivery(bound,enabled=True,current=lambda b:self.current(dict(goal_id=b.goal_id,engine_generation=b.engine_generation,owner_generation=b.owner_generation,stream_id=b.stream_id)),admit=self.admit,project=projection,split=speech_parts,synthesize=synthesize,play=play,clear_output=clear,submit=self.submitted,finalize=lambda *args:None)
    async def offer_task_intent(self,receipt,valid):
        if self.queue is None or not self.current(self.identity):raise CandidateError('task_intent_without_delivery_owner')
        if self.originals or self.terminal_kind is not None:return False
        # Existing submitted language is evidence; never guess from user words.
        for entry in reversed(getattr(self.provider.context.retained,'entries',[])):
            offset=entry.get('offset',0)
            if not offset:continue
            rows=entry.get('speech_segments')
            if rows is not None:
                at=0
                for row in rows:
                    at+=len(row['text'])
                    if offset<=at:
                        if row['language']!='en':return False
                        break
                else:return False
            break
        def admissible(value):
            if not valid(value) or self.provider.pending:return False
            binding=getattr(self.clear_output,'__self__',None);p=getattr(binding,'p',None)
            if p is None or p.controller is not self.controller or p.closing or p.pipeline_failed.is_set():return False
            # The real attached mixer/call owners publish read-only predicates.
            # Missing means that optional lane is not attached; faults suppress.
            for name in ('task_ack_music_active','task_ack_call_held'):
                predicate=getattr(p,name,None)
                if predicate is not None:
                    if not callable(predicate):return False
                    try:
                        blocked=predicate()
                    except Exception:return False
                    if type(blocked) is not bool or blocked:return False
            return True
        if not admissible(receipt):return False
        accepted=await self.queue.offer_control(receipt,valid=admissible,
            synthesize=lambda text:self.controller.task_ack_synthesize(text,self.owner_generation),
            play=lambda pcm:self.controller.task_ack_play(pcm,self.owner_generation))
        if accepted:self.ready.set()
        return accepted
    def receipt_valid(self,receipt,index):
        if type(receipt) is not UnitCommit or receipt.issuer is not self.issuer or receipt.binding!=self.identity or receipt.index!=index or receipt.projection!='source':return False
        interim=dict(kind='unit',binding=dict(self.identity),value=dict(index=receipt.index,language=receipt.language,text=receipt.text))
        return receipt.digest==digest(interim) and self.current(self.identity)
    def admit(self,item,stage):
        if not self.current(self.identity):return False
        if isinstance(item,FullComplete):return self.terminal_kind=='FULL_COMPLETE'
        i=item.index
        return 0<=i<len(self.originals) and self.locals[i] is item and self.receipt_valid(self.originals[i],i)
    async def commit(self,receipt):
        if self.queue is None or not self.receipt_valid(receipt,len(self.originals)):raise CandidateError('native_commit_receipt_not_bound')
        item=Unit(self.queue.binding,receipt.index,receipt.language,receipt.text)
        self.originals.append(receipt);self.locals.append(item)
        # offer validates/enqueues only. It never awaits synthesis/playback.
        await self.queue.offer(item);self.ready.set()
        self.provider.events.emit('validated_unit_queued',generation=self.owner_generation,index=item.index,language=item.language,characters=len(item.text),text_retained=False,audio_released=False)
    async def terminal(self,kind,identity,commits,answer,segments):
        if self.identity!=identity or type(commits) is not tuple or len(commits)!=len(self.originals) or any(a is not b for a,b in zip(commits,self.originals)):raise CandidateError('native_terminal_receipts_not_bound')
        if kind=='DEFERRED':
            if commits or self.terminal_kind is not None:raise CandidateError('whole_final_after_commits')
            self.terminal_kind=kind;self.terminal_answer=answer;self.terminal_segments=copy.deepcopy(segments);return
        if kind=='FULL_COMPLETE':
            if self.terminal_kind is not None or not self.current(identity):raise CandidateError('native_complete_owner_changed')
            rows=validate_segments(segments,available=self.controller.real_clients.speech_languages,text=answer)
            projected=project(rows,available=self.controller.real_clients.speech_languages)
            self.terminal_kind=kind;self.terminal_answer=answer;self.terminal_segments=copy.deepcopy(rows)
            await self.queue.finish(FullComplete(self.queue.binding,len(commits),answer,''.join(r['text'] for r in projected)))
            self.provider.events.emit('validated_delivery_native_complete',generation=self.owner_generation,units=len(commits),speech_complete=False,text_retained=False)
            return
        if kind not in {'PARTIAL_FAILED','FAILED'}:raise CandidateError('native_terminal_kind_invalid')
        self.terminal_kind=kind
        if self.originals:await self.queue.abort('producer_failed')
    def submitted(self,prefix,language,binding):
        if not self.current(self.identity):raise CandidateError('partial_history_owner_changed')
        if self.partial is None:
            self.partial=dict(type='model_output',partial_output=True,content=[dict(type='text',text=prefix)])
            self.controller.history.append(self.partial)
        else:self.partial['content'][0]['text']=prefix
        if self.retained_registered:self.controller.note_reply_submission(self.owner_generation,prefix)
        self.provider.events.emit('assistant_speech_prefix_submitted',generation=self.owner_generation,chars=len(prefix),validated_units=True,acoustic_completion_measured=False)
    def register_native_result(self,result):
        if not self.originals:return
        if result.get('answer')!=self.terminal_answer or result.get('speech_segments')!=self.terminal_segments or self.terminal_kind!='FULL_COMPLETE':raise CandidateError('validated_final_result_mismatch')
        if not self.current(self.identity):raise CandidateError('native_result_owner_changed')
        if self.retained_registered:return
        self.provider.context.retained.remember(result,self.owner_generation)
        self.retained_registered=True
        if self.queue.prefix:self.controller.note_reply_submission(self.owner_generation,self.queue.prefix)
    async def produce(self,operation):
        result=await operation
        self.register_native_result(result)
        return result
    async def settle_producer(self):
        # Owner cancellation must not propagate into native child retirement.
        deadline=asyncio.get_running_loop().time()+5
        settled=asyncio.ensure_future(asyncio.gather(self.task,return_exceptions=True))
        while not settled.done():
            remaining=deadline-asyncio.get_running_loop().time()
            if remaining<=0:raise CandidateError('validated_native_cleanup_unsettled')
            try:
                async with asyncio.timeout(remaining):await asyncio.shield(settled)
            except asyncio.CancelledError:continue
            except TimeoutError:raise CandidateError('validated_native_cleanup_unsettled') from None
        settled.result()
    async def drive(self,operation):
        """Called inline by original turn owner; producer runs separately."""
        self.task=asyncio.create_task(self.produce(operation))
        self.task.add_done_callback(lambda _:self.producer_done.set())
        ready=asyncio.create_task(self.ready.wait())
        try:
            while True:
                await asyncio.wait({ready,self.task},return_when=asyncio.FIRST_COMPLETED)
                if self.originals or self.task.done():break
                if self.queue is not None and self.queue.control is not None:
                    await self.queue.deliver_control()
                self.ready.clear()
                ready.cancel();await asyncio.gather(ready,return_exceptions=True)
                ready=asyncio.create_task(self.ready.wait())
                if self.originals or self.task.done():break
            if self.originals:
                self.outcome=await self.queue.run()
                if self.outcome.status!='complete':
                    if not self.provider.current(self.owner_generation):raise asyncio.CancelledError()
                    if self.task.done():await self.task
                    raise CandidateError('validated_delivery_partial_failure')
            result=await self.task
            if self.originals:
                if result.get('answer')!=self.terminal_answer or result.get('speech_segments')!=self.terminal_segments or self.terminal_kind!='FULL_COMPLETE':raise CandidateError('validated_final_result_mismatch')
            elif self.terminal_kind=='DEFERRED':
                if result.get('answer')!=self.terminal_answer or result.get('speech_segments')!=self.terminal_segments:raise CandidateError('whole_final_result_mismatch')
            return result
        except BaseException:
            try:
                if self.queue is not None and (self.originals or self.queue.control_started):await self.queue.abort('owner_cancelled' if not self.provider.current(self.owner_generation) else 'producer_failed')
            finally:
                if not self.task.done() and self.task.cancelling()==0:self.task.cancel()
                await self.settle_producer()
            raise
        finally:
            ready.cancel();await asyncio.gather(ready,return_exceptions=True)
    def consume(self,text,generation):
        if not self.originals:return None
        if generation!=self.owner_generation:return None
        if self.consumed:return False
        if self.outcome is None or self.outcome.status!='complete' or self.terminal_kind!='FULL_COMPLETE' or text!=self.terminal_answer or not self.current(self.identity):raise CandidateError('validated_speech_completion_not_bound')
        self.consumed=True
        if self.partial is not None:self.controller.history=[row for row in self.controller.history if row is not self.partial]
        display=getattr(self.controller,'display_reply',None)
        if callable(display):display(generation,text,'complete')
        self.controller.note_reply_submission(generation,self.outcome.submitted_prefix)
        self.provider.complete_reply(generation)
        self.provider.events.emit('response_complete',generation=generation,greeting=False,error_notice=False,parts=self.queue.chunks,validated_units=True,acoustic_completion_measured=False)
        return True
