"""Use the existing ASR lifecycle deadline for a brief suspended response."""
import asyncio,base64
from me3_completion.pipeline import Ownership
from me3_completion.policy import annotation,listener_ack
from me3_m7.client import RecoverableASRTimeout

def attach(processor,clients,provider,events):
    decider=getattr(provider,'floor_decider',None);admission=None
    factory=getattr(provider,'warm_floor_factory',None)
    if factory is not None:
        from me3_assistant.warm_floor import WarmFloorLane
        if not callable(factory) or decider is not None:raise ValueError('one_explicit_floor_lane_required')
        lane=factory(processor,provider,events)
        if not isinstance(lane,WarmFloorLane) or lane.p is not processor or lane.provider is not provider:raise ValueError('actual_warm_lane_owner_required')
        processor.warm_floor_lane=lane;decider=lane
    if callable(decider):
        from me3_assistant.floor_admission import FloorAdmission
        admission=FloorAdmission(processor,provider,events,decider,response_state=Ownership.RESPONSE,processing_state=Ownership.PROCESSING)
    processor.floor_admission_enabled=admission is not None;processor.floor_admission=admission
    if admission is not None:
        # Wrap the ACTUAL installed SharedController/CandidateSpeech method,
        # spanning every TTS part. Never derive an owner from Context state.
        original_speak=processor.controller.speak
        original_interrupt=processor.controller.interrupt
        async def owned_speak(text,generation,*args,**flags):
            record=None
            if not flags.get('greeting') and not flags.get('error_notice'):record=processor.begin_floor_response(generation)
            try:return await original_speak(text,generation,*args,**flags)
            finally:processor.end_floor_response(record)
        def invalidate_response():
            processor.invalidate_floor_response();return original_interrupt()
        processor.controller.speak=owned_speak;processor.controller.interrupt=invalidate_response
    async def classify(token,pcm,duration,old_generation,old_task):
        controller=processor.controller
        if admission is not None:
            entry_review=processor.overlap_review_identity()
            if not await processor.wait_floor_retirement(token,old_generation):return
            async with asyncio.timeout(5):
                if not await processor.wait_overlap_onset_settled(token,old_generation,entry_review):return
        if controller.turns>=controller.max_turns or not controller.reserve_overlap_probe():
            await processor.takeover('probe_attempt_budget_exhausted');return
        reserved=old_generation+1;controller.credit_overlap(old_generation)
        events.emit('agent_overlap_deadline',generation=reserved,recognition_deadline_seconds=35,server_operation_seconds=32,cleanup_seconds=2,provider='configured ASR MCP',automatic_retry=False)
        recognized=False;review_identity=processor.overlap_review_identity()
        try:
            async with asyncio.timeout(35):
                data=await clients.call('asr',reserved,pcm_b64=base64.b64encode(pcm).decode(),sample_rate=16000);recognized=True
                settled=await processor.wait_overlap_onset_settled(token,old_generation,review_identity)
        except asyncio.CancelledError:raise
        except (RecoverableASRTimeout,TimeoutError):
            if recognized:raise RuntimeError('overlap_onset_settlement_timeout')
            if not processor.closing and token==processor.token and controller.clock.current==old_generation and controller.current(old_generation) and not processor.valid(token):
                raise RuntimeError('overlap_recognition_failed_during_onset')
            if processor.valid(token) and controller.clock.current==old_generation:
                controller.turns+=1;await processor.takeover('overlap_recognition_timeout');processor.pcm.clear();processor.preroll.clear()
                controller.spawn(controller.speak('I could not resolve that interruption. Please say your next complete request.',controller.clock.current,'normal',error_notice=True))
            return
        if not settled or not processor.valid(token) or controller.clock.current!=old_generation:
            events.emit('completion_overlap_result_stale',generation=reserved,token=token);return
        text=data['text'];ack=listener_ack(text,duration,task_pending=bool(getattr(provider,'pending',None)))
        events.emit('completion_overlap_classified',generation=reserved,token=token,**annotation(text,duration),semantic_ack=ack,text_retained=False)
        if admission is None and ack and processor.output_task is old_task and processor.output_kind=='response' and processor.output_generation==old_generation:
            controller.listener_ack_count+=1;processor.pcm.clear();processor.preroll.clear();processor.probing=False;processor.gate.resume()
            processor.transition(Ownership.RESPONSE,'confirmed_listener_ack_resume_suspended_response')
            events.emit('completion_user_backchannel',generation=old_generation,takeover=0,response_suspended_not_canceled=True,capture_count=processor.state.capture_count);return
        if admission is not None:
            await admission.admit(token,pcm,duration,old_generation,old_task,data,review_identity=review_identity);return
        generation=await processor.takeover('recognized_substantive_overlap');processor.pcm.clear();processor.preroll.clear()
        if generation!=reserved:raise RuntimeError('overlap_generation_reservation_mismatch')
        processor.transition(Ownership.PROCESSING,'adopt_validated_overlap_asr');controller.submit_recognized(pcm,generation,data)
    processor.classify_overlap=classify
