"""Host-only opt-in binding to the existing processor and final output epoch.

Reset acknowledgment means software CallbackOutput retirement, never acoustic
cessation. A stale delivery can observe a newer accepted reset, never issue one.
"""
import asyncio
from functools import wraps
from pipecat.frames.frames import InterruptionFrame
from onit_candidate.protocol import CandidateError

def bind(processor,conversation,*,enabled=False):
    if type(enabled) is not bool:raise CandidateError('validated_delivery_boolean_required')
    if not enabled:return None
    if getattr(processor,'validated_output_binding',None) is not None:raise CandidateError('validated_output_already_bound')
    if conversation.controller is not processor.controller or not conversation.voice:raise CandidateError('validated_output_owner_required')
    binding=OutputBinding(processor,conversation)
    processor.validated_output_binding=binding
    conversation.configure_validated_delivery(enabled=True,clear_output=binding.clear)
    return binding

class OutputBinding:
    def __init__(self,processor,conversation):
        self.p=processor;self.c=conversation;self.token=object();self.receipts=[];self.owners=[];self.reset_changed=asyncio.Event()
        output=processor.output_transport;original=output.process_frame
        async def observed(frame,direction):
            request=getattr(frame,'m3_validated_reset',None)
            before=processor.state.buffer.epoch;generation=processor.controller.clock.current
            if request is not None:
                if (request.get('token') is not self.token or request.get('generation')!=generation
                    or not processor.controller.current(generation)):
                    request['accepted']=False;request['done'].set();return
            try:
                await original(frame,direction)
                after=processor.state.buffer.epoch
                accepted=(isinstance(frame,InterruptionFrame) and after>before
                    and processor.controller.clock.current==generation)
                if accepted:
                    self.receipts.append((generation,after));self.receipts=self.receipts[-32:];self.reset_changed.set()
                if request is not None:request['accepted']=accepted
            finally:
                if request is not None:request['done'].set()
        output.process_frame=observed
        admission=getattr(processor,'floor_admission',None)
        if admission is not None:
            if getattr(admission,'p',None) is not processor or getattr(admission,'provider',None) is not conversation:
                raise CandidateError('validated_floor_admission_owner_required')
            original_generated=admission.current_generated_text
            def generated(generation,maximum=1200):
                sink=conversation._early_delivery
                if sink is None or sink.identity is None or sink.terminal_kind=='DEFERRED':
                    return original_generated(generation,maximum)
                # A selected early response never falls back to an older answer
                # if any current ownership/receipt check fails. This is generated
                # source context only, never a claim that audio was heard.
                if type(maximum) is not int or maximum<=0:return ''
                identity=sink.identity
                if (sink.provider is not conversation or sink.controller is not processor.controller
                    or sink.owner_generation!=generation or getattr(conversation,'pending',None) is not None
                    or not self.identity_owned(identity) or not sink.current(identity)
                    or processor.floor_response_task(generation) is not processor.controller.task
                    or any(not sink.receipt_valid(receipt,index) for index,receipt in enumerate(sink.originals))):
                    return ''
                queue=sink.queue
                if queue is not None and queue.control_playing:
                    receipt=queue.control_receipt
                    if receipt is None or not callable(queue.control_valid) or not queue.control_valid(receipt):return ''
                    # Current spoken control context only. Never final/history,
                    # an unspoken unit, retained answer, or confirmation data.
                    return receipt.text[-min(maximum,1200):]
                return ''.join(receipt.text for receipt in sink.originals)[-min(maximum,1200):]
            admission.current_generated_text=generated
        # Early committed speech runs inside answer(), before the existing
        # normal owned_speak wrapper. Bind the same original controller owner
        # across planning/early playback and native cleanup; release it before
        # ordinary speak opens its own record (including WHOLE/review paths).
        original_answer=conversation.answer
        @wraps(original_answer)
        async def owned_answer(*args,**kwargs):
            enabled=processor.floor_admission_enabled
            if type(enabled) is not bool:raise CandidateError('validated_floor_boolean_required')
            if not enabled:return await original_answer(*args,**kwargs)
            if admission is None or processor.floor_admission is not admission:
                raise CandidateError('validated_floor_admission_binding_changed')
            controller=processor.controller;generation=controller.clock.current
            if (conversation.controller is not controller or asyncio.current_task() is not controller.task
                or not controller.current(generation)):
                raise CandidateError('validated_floor_original_owner_required')
            record=processor.begin_floor_response(generation)
            succeeded=False;answer=None
            try:
                if (type(record) is not dict or processor.floor_response is not record
                    or record.get('generation')!=generation or record.get('task') is not controller.task
                    or record.get('task') is not asyncio.current_task() or record.get('started') is not False):
                    raise CandidateError('validated_floor_record_required')
                conversation.events.emit('validated_answer_floor_bound',generation=generation,
                    original_controller_owner=True,first_PCM_started=False)
                answer=await original_answer(*args,**kwargs);succeeded=True
                return answer
            finally:
                sink=conversation._early_delivery
                handoff=(succeeded and isinstance(answer,str) and bool(answer.strip())
                    and not (sink is not None and sink.originals)
                    and processor.floor_response is record and record.get('task') is controller.task
                    and controller.task is asyncio.current_task() and not controller.task.done()
                    and not controller.task.cancelling() and controller.current(generation)
                    and not controller.closed and not processor.closing and not processor.pipeline_failed.is_set())
                if handoff:
                    record['awaiting_speech']=True
                    def unconsumed(task):
                        if processor.floor_response is not record or record.get('awaiting_speech') is not True:return
                        record.pop('awaiting_speech')
                        try:
                            processor.end_floor_response(record)
                            conversation.events.emit('validated_answer_handoff_unconsumed',generation=generation,cancelled=task.cancelled(),exact_record_cleanup=True)
                        except BaseException as error:
                            processor.pipeline_failed.set()
                            conversation.events.emit('validated_answer_handoff_cleanup_failed',generation=generation,error_type=type(error).__name__)
                    controller.task.add_done_callback(unconsumed)
                    conversation.events.emit('validated_answer_floor_handoff',generation=generation,first_PCM_started=bool(record.get('started')),same_controller_owner=True,speech_completed=False)
                else:
                    processor.end_floor_response(record)
                    conversation.events.emit('validated_answer_floor_released',generation=generation,
                        first_PCM_started=bool(type(record) is dict and record.get('started')),
                        exact_record_cleanup=True)
        conversation.answer=owned_answer
    def register(self,sink,identity):
        if (sink is not self.c._early_delivery or sink.controller is not self.p.controller
            or sink.provider is not self.c or sink.identity!=identity or not sink.current(identity)):return False
        self.owners.append((sink,dict(identity)));self.owners=self.owners[-32:]
        return True
    def identity_owned(self,identity):
        return bool(type(identity) is dict and any(saved==identity and sink.identity==saved
            and sink.controller is self.p.controller and sink.provider is self.c for sink,saved in self.owners))
    def retired(self,generation):
        return bool(self.p.controller.clock.current>generation
            and self.p.output_generation!=generation
            and any(g>generation and epoch<=self.p.state.buffer.epoch for g,epoch in self.receipts))
    async def wait_retired(self,generation,*,deadline=None):
        loop=asyncio.get_running_loop()
        deadline=min(deadline if deadline is not None else loop.time()+5,loop.time()+5)
        if loop.time()>=deadline:return False
        if self.retired(generation):return True
        self.c.events.emit('validated_delivery_retirement_wait',generation=generation,
            replacement_generation=self.p.controller.clock.current,maximum_seconds=5,
            sends_reset=False,acoustic_completion_measured=False)
        verified=False
        try:
            async with asyncio.timeout_at(deadline):
                while not self.retired(generation):
                    # Reset receipts are emitted only by the real final output
                    # observer. Recheck after clearing to avoid a lost wakeup.
                    self.reset_changed.clear()
                    if self.retired(generation):break
                    await self.reset_changed.wait()
            verified=self.retired(generation)
        except TimeoutError:verified=False
        finally:
            self.c.events.emit('validated_delivery_retirement_wait_settled',generation=generation,
                verified=verified,sends_reset=False,acoustic_completion_measured=False)
        return verified
    async def clear(self,identity):
        if not self.identity_owned(identity):return False
        generation=identity.get('owner_generation')
        if type(generation) is not int:return False
        deadline=asyncio.get_running_loop().time()+5
        if not self.p.controller.current(generation):return await self.wait_retired(generation,deadline=deadline)
        # The queue has already settled its play coroutine before invoking us.
        # Never acquire output_lock or await controller.task from its abort path.
        if self.p.output_generation not in (None,generation):return False
        request=dict(token=self.token,generation=generation,done=asyncio.Event(),accepted=False)
        frame=InterruptionFrame();frame.m3_generation=generation;frame.m3_validated_reset=request
        if self.p.floor_admission_enabled:self.p.invalidate_floor_response()
        async with asyncio.timeout_at(deadline):
            await self.p.push_frame(frame)
            await request['done'].wait()
        if request['accepted'] and self.p.controller.current(generation):
            self.p.gate.resume()
            self.c.events.emit('validated_delivery_output_retired',generation=generation,
                epoch=self.p.state.buffer.epoch,acoustic_completion_measured=False)
            return True
        if self.p.controller.clock.current>generation:return await self.wait_retired(generation,deadline=deadline)
        return False
