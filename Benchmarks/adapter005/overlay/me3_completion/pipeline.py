"""Local endpoint and explicit ownership around the accepted MCP/controller/audio path."""
import asyncio,base64,os,time
from enum import Enum
class Ownership(str,Enum):
    LISTENING="listening"
    USER="user_holds_turn"
    HOLD="pause_hold"
    PROCESSING="processing_user_turn"
    RESPONSE="agent_response"
    BACKCHANNEL="agent_listener_backchannel"
    SUSPENDED="response_suspended_for_classification"
    CLOSED="closed"
from collections import deque
from voice_agent import output_channel_adapter
from speech_mcp.contracts import decode_pcm,human_turn_limit,MAX_TTS_SECONDS
from speech_mcp.diagnostics import make_fault_observer
from me3_m7.client import RecoverableASRTimeout
from me3_completion.controller import ConversationController
from me3_completion.endpoint import LocalEndpoint
from me3_completion.playback import SuspendableBuffer,PlaybackGate
from me3_completion.policy import annotation,listener_ack
from me3_completion.cache import CUE

def build_pipeline(settings,events,clients,provider,*,transport_factory=None):
    from duplex_audio import make_duplex_transport
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from pipecat.audio.vad.vad_analyzer import VADParams,VADState
    from pipecat.frames.frames import InputAudioRawFrame,OutputAudioRawFrame,InterruptionFrame,StartFrame,CancelFrame,EndFrame,TTSStartedFrame,TTSStoppedFrame,LLMFullResponseStartFrame,LLMFullResponseEndFrame
    from pipecat.processors.frame_processor import FrameProcessor,FrameDirection
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.worker import PipelineParams,PipelineWorker
    from pipecat.workers.runner import WorkerRunner
    transport,state=(make_duplex_transport if transport_factory is None else transport_factory)(settings,events)
    state.buffer=SuspendableBuffer(state.buffer);gate=PlaybackGate(state.buffer)
    class ConversationProcessor(FrameProcessor):
        def __init__(self):
            super().__init__()
            self.gate=gate;self.state=state
            self.vad=SileroVADAnalyzer(params=VADParams(confidence=.7,start_secs=.15,stop_secs=.7,min_volume=.6));self.vad.set_sample_rate(16000)
            self.endpoint=LocalEndpoint(events);self.pipeline_failed=asyncio.Event()
            self.overlap_onset_settled=asyncio.Event();self.overlap_onset_settled.set()
            self.controller=ConversationController(clients,provider,events,self.play,max_turns=human_turn_limit(os.getenv('M3_MAX_HUMAN_TURNS','8')))
            self.controller.listener_cue=self.semantic_cue;self.last_cue_at=-1000
            self.max_pcm_bytes=480000;self.preroll=deque(maxlen=15);self.pcm=bytearray();self.speaking=False;self.overflow=False;self.probing=False
            self.token=0;self.cursor=0;self.onset=None;self.active_end=0;self.starting=None;self.last_vad=None
            self.jobs=set();self.decision=None;self.probe=None;self.cue=None;self.cued=False
            self.floor_admission_enabled=False;self.floor_retiring=set();self.floor_retirement=None;self.floor_response=None;self.warm_floor_lane=None
            self.output_generation=None;self.output_kind=None;self.output_task=None;self.output_lock=asyncio.Lock();self.closing=False;self.ownership=Ownership.LISTENING
        def spawn(self,coroutine):
            task=asyncio.create_task(coroutine);self.jobs.add(task)
            def done(job):
                self.jobs.discard(job)
                if not job.cancelled() and job.exception() is not None:
                    events.emit('completion_background_error',error_type=type(job.exception()).__name__);self.pipeline_failed.set()
            task.add_done_callback(done);return task
        def transition(self,state,reason):
            if not isinstance(state,Ownership):raise ValueError('invalid_ownership_state')
            previous=self.ownership;self.ownership=state
            events.emit('completion_ownership',generation=self.controller.clock.current,previous=previous.value,current=state.value,reason=reason)
        def valid(self,token):
            if getattr(self,'floor_admission_enabled',False) and self.pipeline_failed.is_set():return False
            return not self.closing and token==self.token and not self.speaking and self.last_vad!=VADState.STARTING and self.controller.current(self.controller.clock.current)
        def overlap_review_identity(self):
            pending=getattr(provider,'pending',None)
            return (bool(pending),pending.get('operation_id'),pending.get('expected_digest')) if isinstance(pending,dict) else (bool(pending),None,None)
        async def wait_overlap_onset_settled(self,token,generation,expected_review=None):
            # A provisional onset is not a new owner. Retain the exact completed
            # ASR result within its existing deadline until VAD settles locally.
            identity=self.overlap_review_identity() if expected_review is None else expected_review
            deferred=False
            def owned():
                return not self.closing and token==self.token and self.controller.clock.current==generation and self.controller.current(generation)
            if not owned():return False
            if self.overlap_review_identity()!=identity:
                raise RuntimeError('overlap_review_changed_during_recognition')
            while owned() and self.last_vad==VADState.STARTING:
                if not deferred:events.emit('completion_overlap_onset_deferred',generation=generation,token=token,transcript_retained=False,duplicate_ASR=False)
                deferred=True;self.overlap_onset_settled.clear()
                await self.overlap_onset_settled.wait()
            if not owned():return False
            if self.overlap_review_identity()!=identity:
                raise RuntimeError('overlap_review_changed_during_onset_settlement')
            if deferred:events.emit('completion_overlap_onset_settled',generation=generation,token=token,transcript_retained=False,duplicate_ASR=False)
            return self.valid(token)
        def retire_floor_probe(self,probe):
            # Called synchronously by capture. Never await cleanup here and
            # never cancel the suspended response or advance its generation.
            probe.cancel();self.floor_retiring.add(probe)
            if self.floor_retirement is None or self.floor_retirement.done():
                if self.floor_retirement is not None and not self.floor_retirement.cancelled() and self.floor_retirement.exception() is not None:
                    raise RuntimeError('floor_prior_retirement_failed')
                self.floor_retirement=self.spawn(self.retire_floor_probes())
        async def retire_floor_probes(self):
            deadline=asyncio.get_running_loop().time()+5
            while self.floor_retiring:
                batch=set(self.floor_retiring);self.floor_retiring.difference_update(batch)
                done,pending=await asyncio.wait(batch,timeout=max(0,deadline-asyncio.get_running_loop().time()))
                if pending:
                    for job in pending:job.cancel()
                    raise RuntimeError('floor_probe_retirement_unsettled')
                if any(not job.cancelled() and job.exception() is not None for job in done):raise RuntimeError('floor_probe_retirement_failed')
                released=getattr(clients,'released',None)
                if not callable(released):raise RuntimeError('floor_ASR_release_barrier_required')
                release=self.spawn(released())
                try:
                    done,pending=await asyncio.wait({release},timeout=max(0,deadline-asyncio.get_running_loop().time()))
                    if pending:raise RuntimeError('floor_ASR_release_unsettled')
                    await release
                finally:
                    if not release.done():release.cancel()
            events.emit('floor_probe_retired',generation=self.controller.clock.current,provider_release_verified=True,automatic_retry=False)
        async def wait_floor_retirement(self,token,generation):
            def owned():return not self.closing and not self.pipeline_failed.is_set() and token==self.token and self.controller.clock.current==generation and self.controller.current(generation)
            if not owned():return False
            barrier=self.floor_retirement
            while barrier is not None:
                await asyncio.shield(barrier)
                if not owned():return False
                if barrier is self.floor_retirement:break
                barrier=self.floor_retirement
            return owned()
        async def retire_floor_handoff(self,token,generation):
            # Caller has already invalidated the old generation/output. Do not
            # admit a replacement until every old job and ASR lease settles.
            def owned():return not self.closing and token==self.token and self.controller.current(generation) and self.controller.clock.current==generation
            if self.pipeline_failed.is_set():raise RuntimeError('floor_handoff_prior_failure')
            if not owned():return False
            deadline=asyncio.get_running_loop().time()+5
            jobs={job for job in self.jobs if job is not asyncio.current_task() and not job.done()}
            for job in jobs:job.cancel()
            if jobs:
                try:done,pending=await asyncio.wait(jobs,timeout=max(0,deadline-asyncio.get_running_loop().time()))
                except BaseException:self.pipeline_failed.set();raise
                if pending or any(not job.cancelled() and job.exception() is not None for job in done):
                    for job in pending:job.cancel()
                    self.pipeline_failed.set();raise RuntimeError('floor_handoff_jobs_unsettled')
            if not owned():return False
            lane=self.warm_floor_lane
            if lane is not None:
                # Warm creation/cleanup belongs to the lane, not self.jobs.
                # Do not cancel its cleanup or start a replacement over it.
                while lane.retirement is not None:
                    barrier=lane.retirement
                    done,pending=await asyncio.wait({barrier},timeout=max(0,deadline-asyncio.get_running_loop().time()))
                    if pending:
                        self.pipeline_failed.set();raise RuntimeError('floor_warm_retirement_unsettled')
                    try:await barrier
                    except BaseException:self.pipeline_failed.set();raise
                    if not owned():return False
                    if lane.retirement is barrier:break
            released=getattr(clients,'released',None)
            if not callable(released):self.pipeline_failed.set();raise RuntimeError('floor_ASR_release_barrier_required')
            release=self.spawn(released())
            try:
                done,pending=await asyncio.wait({release},timeout=max(0,deadline-asyncio.get_running_loop().time()))
                if pending:self.pipeline_failed.set();raise RuntimeError('floor_handoff_ASR_release_unsettled')
                await release
            except BaseException:
                self.pipeline_failed.set();raise
            finally:
                if not release.done():release.cancel()
            if not owned():return False
            if self.pipeline_failed.is_set():raise RuntimeError('floor_handoff_prior_failure')
            self.floor_retiring.clear();self.floor_retirement=None
            admission=getattr(self,'floor_admission',None)
            if admission is not None:admission.cancel_timer();admission.held=None
            events.emit('floor_handoff_retired',generation=generation,provider_release_verified=True,automatic_retry=False)
            return True
        def begin_floor_response(self,generation):
            task=asyncio.current_task()
            if not self.floor_admission_enabled or self.closing or self.pipeline_failed.is_set() or not self.controller.current(generation) or task is not self.controller.task:return None
            if self.floor_response is not None:
                record=self.floor_response
                if (record.get('awaiting_speech') is True and record.get('generation')==generation
                    and record.get('task') is task and not task.done() and not task.cancelling()
                    and not self.controller.closed):
                    record.pop('awaiting_speech')  # host-only, one exact same-owner continuation
                    return record
                raise RuntimeError('floor_response_owner_already_bound')
            record=dict(generation=generation,task=task,started=False)
            self.floor_response=record;return record
        def invalidate_floor_response(self):
            record=self.floor_response;self.floor_response=None
            if self.warm_floor_lane is not None and record is not None:self.warm_floor_lane.invalidate(record)
        def end_floor_response(self,record):
            if record is None or self.floor_response is not record:return
            if self.warm_floor_lane is not None:self.warm_floor_lane.invalidate(record)
            self.floor_response=None
            if (self.probing or not self.gate.ready.is_set()) and not self.closing and self.controller.current(record['generation']):
                # Synthesis ended/failed while an unadmitted overlap held output.
                # No old answer or captured candidate becomes a new command.
                self.token+=1;self.pipeline_failed.set();self.pcm.clear();self.preroll.clear();self.gate.resume()
                self.transition(Ownership.CLOSED,'floor_response_ended_during_admission')
        def floor_response_task(self,generation):
            record=self.floor_response
            if record is not None and record['started'] and record['generation']==generation and record['task'] is self.controller.task and not record['task'].done() and self.controller.current(generation):return record['task']
            return None
        def floor_response_matches(self,task,generation):
            return task is not None and self.floor_response_task(generation) is task
        async def play(self,pcm,generation,kind='response',*,planning_ack_token=None):
            async with self.output_lock:
                acknowledgment=getattr(self,'planning_ack',None)
                if kind=='planning_ack' and (acknowledgment is None or not acknowledgment.output_owned(planning_ack_token,generation)):return
                self.output_generation=generation;self.output_kind=kind;self.output_task=asyncio.current_task()
                self.transition(Ownership.BACKCHANNEL if kind=='backchannel' else Ownership.RESPONSE,'output_started')
                async def emit(frame):
                    frame.m3_generation=generation
                    if kind=='planning_ack':frame.m3_planning_ack_token=planning_ack_token
                    await self.push_frame(frame)
                try:
                    await emit(LLMFullResponseStartFrame());await emit(TTSStartedFrame());first=True
                    loop=asyncio.get_running_loop();deadline=None
                    for offset in range(0,len(pcm),960):
                        ready=gate.ready.is_set()
                        await gate.wait()
                        if not self.controller.current(generation):return
                        audio=pcm[offset:offset+960];duration=len(audio)/48000
                        now=loop.time()
                        # Pace actual PCM against one clock, including push cost.
                        # Resume starts afresh; a late writer may catch up at most
                        # one frame, never replay an accumulated pause or stall.
                        if deadline is None or not ready:deadline=now
                        deadline=max(deadline,now-duration)+duration
                        await emit(OutputAudioRawFrame(audio=audio,sample_rate=24000,num_channels=1));events.output_frames+=1
                        if first:
                            record=self.floor_response
                            if self.floor_admission_enabled and kind in {'response','task_ack'} and record is not None and record['generation']==generation and record['task'] is asyncio.current_task() and record['task'] is self.controller.task and self.controller.current(generation) and not record['started']:
                                record['started']=True
                                if self.warm_floor_lane is not None:self.warm_floor_lane.first_pcm(record)
                            events.emit('generation_playout_started',generation=generation,kind=kind,capture_count=state.capture_count);first=False
                        # A blocked push cannot leave a long catch-up debt.
                        if loop.time()-deadline>duration:deadline=loop.time()
                        await asyncio.sleep(max(0,deadline-loop.time()))
                    if getattr(self,'floor_admission_enabled',False):await gate.wait()
                    if self.controller.current(generation):
                        await emit(TTSStoppedFrame());await emit(LLMFullResponseEndFrame())
                        events.emit('generation_playout_submitted',generation=generation,kind=kind,capture_count=state.capture_count)
                finally:
                    self.output_generation=None;self.output_kind=None;self.output_task=None
                    if self.ownership in {Ownership.RESPONSE,Ownership.BACKCHANNEL}:self.transition(Ownership.HOLD if self.pcm else Ownership.LISTENING,'output_settled')
        async def clear_output(self,reason):
            if self.floor_admission_enabled:self.invalidate_floor_response()
            await self.push_frame(InterruptionFrame());gate.resume()
            events.emit('completion_output_invalidated',generation=self.controller.clock.current,reason=reason,capture_count=state.capture_count)
        async def takeover(self,reason):
            old=self.controller.clock.current;g=self.controller.interrupt();self.probing=False
            self.transition(Ownership.USER,reason)
            await self.clear_output(reason)
            events.emit('completion_takeover',generation=g,previous_generation=old,reason=reason,capture_count=state.capture_count)
            return g
        async def startup(self):
            # Actual MCP priming, bounded local synthesis, no prerecorded reply or agent-side PCM cache.
            events.emit('completion_cue_prime_start',generation=0)
            data=await clients.call('tts',0,text=CUE,style='normal');pcm=decode_pcm(data['pcm_b64'],24000,MAX_TTS_SECONDS)
            if len(pcm)>=48000:raise ValueError('backchannel_over_one_second')
            events.emit('completion_cue_primed',generation=0,generated_seconds=len(pcm)/48000,audible=False)
            if self.controller.clock.current==0 and not self.closing:self.controller.greet()
        async def semantic_cue(self,generation):
            if self.speaking or not self.controller.current(generation) or time.monotonic()-self.last_cue_at<6:return
            data=await clients.call('tts',generation,text=CUE,style='normal')
            if self.speaking or not self.controller.current(generation):return
            pcm=decode_pcm(data['pcm_b64'],24000,MAX_TTS_SECONDS)
            if len(pcm)>=48000:raise ValueError('backchannel_over_one_second')
            self.last_cue_at=time.monotonic()
            events.emit('completion_agent_backchannel',generation=generation,source='recognized_explicit_turn_hold',generated_seconds=len(pcm)/48000,word_count=1,takeover=0)
            await self.play(pcm,generation,'backchannel')
        async def backchannel(self,token):
            g=self.controller.clock.current
            data=await clients.call('tts',g,text=CUE,style='normal')
            if not self.valid(token) or not self.controller.current(g):return
            pcm=decode_pcm(data['pcm_b64'],24000,MAX_TTS_SECONDS)
            if len(pcm)>=48000:raise ValueError('backchannel_over_one_second')
            if time.monotonic()-self.last_cue_at<6:return
            self.last_cue_at=time.monotonic()
            events.emit('completion_agent_backchannel',generation=g,token=token,generated_seconds=len(pcm)/48000,word_count=1,takeover=0,semantic_content='neutral_listener_acknowledgment')
            await self.play(pcm,g,'backchannel')
        async def decide(self,token,snapshot):
            start=time.monotonic();complete=await self.endpoint.decide(snapshot[-480000:],token)
            if not self.valid(token):events.emit('completion_endpoint_stale',token=token);return
            if not complete:
                self.transition(Ownership.HOLD,'neural_endpoint_incomplete')
                events.emit('completion_turn_held',generation=self.controller.clock.current,token=token,maximum_total_silence_seconds=3)
                if getattr(self,'allow_speculative_pause_cues',True) and not self.cued and (self.active_end-self.onset)/16000>=1.5 and not getattr(provider,'pending',None):
                    self.cued=True;self.cue=self.spawn(self.backchannel(token))
                # Total bounded silence includes the unchanged0.7s VAD stop period and model time.
                await asyncio.sleep(max(0,3-.7-(time.monotonic()-start)))
                if not self.valid(token):return
                events.emit('completion_hold_expired',generation=self.controller.clock.current,token=token,policy='submit actual retained audio; ASR ambiguity remains explicit')
            await self.commit(token,'endpoint_complete' if complete else 'bounded_hold_expired')
        async def commit(self,token,reason):
            if not self.valid(token):return
            if self.cue and not self.cue.done():
                self.cue.cancel();await self.takeover('backchannel_yields_to_completed_user_turn')
            pcm=bytes(self.pcm);self.pcm.clear();self.preroll.clear();self.cued=False
            if self.overflow:
                self.overflow=False;self.controller.spawn(self.controller.speak('That request was too long. Please give one shorter complete request.',self.controller.clock.current,'normal',error_notice=True));return
            if len(pcm)<3200:return
            events.emit('utterance_committed',generation=self.controller.clock.current,byte_count=len(pcm),endpoint_reason=reason,token=token)
            self.transition(Ownership.PROCESSING,reason)
            self.controller.submit(pcm,self.controller.clock.current)
        async def classify_overlap(self,token,pcm,duration,old_generation,old_task):
            if self.controller.turns>=self.controller.max_turns:
                events.emit('turn_budget_reached',max_turns=self.controller.max_turns);await self.takeover('probe_budget_exhausted');return
            if not self.controller.reserve_overlap_probe():await self.takeover('probe_attempt_budget_exhausted');return
            reserved=old_generation+1;recognized=False;review_identity=self.overlap_review_identity()
            try:
                async with asyncio.timeout(8):
                    data=await clients.call('asr',reserved,pcm_b64=base64.b64encode(pcm).decode(),sample_rate=16000);recognized=True
                    settled=await self.wait_overlap_onset_settled(token,old_generation,review_identity)
            except asyncio.CancelledError:raise
            except (RecoverableASRTimeout,TimeoutError):
                if recognized:raise RuntimeError('overlap_onset_settlement_timeout')
                if not self.closing and token==self.token and self.controller.clock.current==old_generation and self.controller.current(old_generation) and not self.valid(token):
                    raise RuntimeError('overlap_recognition_failed_during_onset')
                if self.valid(token):
                    self.controller.turns+=1;await self.takeover('overlap_recognition_timeout');self.pcm.clear()
                    self.controller.spawn(self.controller.speak('I could not resolve that interruption. Please say your next complete request.',self.controller.clock.current,'normal',error_notice=True))
                return
            if not settled or not self.valid(token) or self.controller.clock.current!=old_generation:
                events.emit('completion_overlap_result_stale',generation=reserved,token=token);return
            text=data['text'];note=annotation(text,duration)
            ack=listener_ack(text,duration,task_pending=bool(getattr(provider,'pending',None)))
            events.emit('completion_overlap_classified',generation=reserved,token=token,**note,semantic_ack=ack,text_retained=False)
            if ack and self.output_task is old_task and self.output_kind=='response' and self.output_generation==old_generation:
                self.controller.listener_ack_count+=1;self.pcm.clear();self.preroll.clear();self.probing=False;gate.resume()
                self.transition(Ownership.RESPONSE,'confirmed_listener_ack_resume_suspended_response')
                events.emit('completion_user_backchannel',generation=old_generation,takeover=0,response_suspended_not_canceled=True,capture_count=state.capture_count);return
            g=await self.takeover('recognized_substantive_overlap');self.pcm.clear();self.preroll.clear()
            if g!=reserved:raise RuntimeError('overlap_generation_reservation_mismatch')
            self.transition(Ownership.PROCESSING,'adopt_validated_overlap_asr')
            self.controller.submit_recognized(pcm,g,data)
        async def process_frame(self,frame,direction):
            await super().process_frame(frame,direction)
            if direction==FrameDirection.UPSTREAM:await self.push_frame(frame,direction);return
            if isinstance(frame,StartFrame):
                await self.push_frame(frame,direction)
                if clients is not None:self.spawn(self.startup())
                return
            if isinstance(frame,(CancelFrame,EndFrame)):
                await self.shutdown();await self.push_frame(frame,direction);return
            if not isinstance(frame,InputAudioRawFrame):await self.push_frame(frame,direction);return
            if frame.sample_rate!=16000 or frame.num_channels!=1:raise ValueError('unexpected_capture_format')
            if getattr(self,'floor_admission_enabled',False) and self.pipeline_failed.is_set():return
            events.input_frames+=1;self.cursor+=len(frame.audio)//2
            held=bool(self.pcm) or self.overflow
            if held and not self.overflow:self.pcm.extend(frame.audio)
            if not held and not self.overflow:self.preroll.append(frame.audio)
            activity=await self.vad.analyze_audio(frame.audio);previous_vad=self.last_vad
            background=getattr(self,'background_delivery',None)
            if background is not None and activity in {VADState.STARTING,VADState.SPEAKING}:await background.user_onset()
            acknowledgment=getattr(self,'planning_ack',None)
            if acknowledgment is not None and activity in {VADState.STARTING,VADState.SPEAKING}:acknowledgment.user_onset()
            if activity==VADState.STARTING:self.overlap_onset_settled.clear()
            elif activity in {VADState.QUIET,VADState.SPEAKING}:self.overlap_onset_settled.set()
            if activity!=self.last_vad:events.emit('completion_vad',state=activity.name,generation=self.controller.clock.current,capture_count=state.capture_count);self.last_vad=activity
            if activity==VADState.STARTING and self.starting is None:self.starting=self.cursor-len(frame.audio)//2
            if activity in {VADState.STARTING,VADState.SPEAKING}:self.active_end=self.cursor
            if not self.speaking and activity==VADState.SPEAKING:
                self.speaking=True;self.token+=1
                if self.decision and not self.decision.done():self.decision.cancel()
                if self.probe and not self.probe.done():
                    if getattr(self,'floor_admission_enabled',False) and self.probing:
                        self.retire_floor_probe(self.probe)
                    else:self.probe.cancel();await self.takeover('additional_speech_during_overlap_probe')
                if self.cue and not self.cue.done():self.cue.cancel();await self.takeover('listener_backchannel_yields_to_user')
                if not held:
                    self.pcm=bytearray(b''.join(self.preroll));self.preroll.clear();self.overflow=False;self.cued=False
                    self.onset=self.starting if self.starting is not None else self.cursor-2400
                    if (self.floor_response_task(self.controller.clock.current) is not None) if self.floor_admission_enabled else (self.output_kind=='response' and self.output_generation==self.controller.clock.current):
                        self.probing=True;gate.suspend()
                        self.transition(Ownership.SUSPENDED,'overlap_onset')
                        events.emit('completion_response_suspended',generation=self.controller.clock.current,reason='classify brief acknowledgment or takeover',capture_count=state.capture_count)
                    else:await self.takeover('new_user_turn')
                else:
                    if getattr(self,'floor_admission_enabled',False) and self.probing:self.transition(Ownership.SUSPENDED,'unadmitted_overlap_continuation')
                    else:self.transition(Ownership.USER,'continuation_after_pause')
                    events.emit('completion_continuation_captured',generation=self.controller.clock.current,token=self.token,byte_count=len(self.pcm))
                events.emit('completion_user_onset',generation=self.controller.clock.current,token=self.token,observed_ns=time.monotonic_ns(),method='VAD confirmed onset proxy')
            if self.speaking and self.probing and self.onset is not None and (self.active_end-self.onset)/16000>=1 and not getattr(self,'floor_admission_enabled',False):await self.takeover('overlap_exceeds_brief_duration')
            if len(self.pcm)>self.max_pcm_bytes:
                self.pcm.clear();self.preroll.clear();self.overflow=True
                events.emit('utterance_limit_reached',generation=self.controller.clock.current,retention='explicitly rejected whole overlong turn; no partial action')
                if getattr(self,'floor_admission_enabled',False) and self.probing:
                    self.pipeline_failed.set()
                    await self.takeover('floor_overlap_resource_limit_no_admission')
                    self.transition(Ownership.CLOSED,'floor_resource_failure_no_admission')
                    raise RuntimeError('floor_overlap_limit_no_partial_admission')
            if self.speaking and activity==VADState.QUIET:
                self.speaking=False;self.starting=None
                events.emit('completion_vad_endpoint',generation=self.controller.clock.current,token=self.token,byte_count=len(self.pcm),method='VAD quiet decision proxy')
                if self.overflow:
                    self.decision=self.spawn(self.commit(self.token,'input_limit'));return
                if len(self.pcm)<3200:return
                if self.probing:
                    duration=max(0,(self.active_end-self.onset)/16000)
                    self.probe=self.spawn(self.classify_overlap(self.token,bytes(self.pcm),duration,self.controller.clock.current,self.floor_response_task(self.controller.clock.current) if self.floor_admission_enabled else self.output_task))
                else:self.decision=self.spawn(self.decide(self.token,bytes(self.pcm)))
            elif activity==VADState.QUIET and not self.speaking:
                self.starting=None
                # A failed STARTING pulse must not lose a deferred endpoint decision.
                if self.pcm and previous_vad==VADState.STARTING and not self.probing:
                    self.token+=1
                    if self.decision and not self.decision.done():self.decision.cancel()
                    self.decision=self.spawn(self.decide(self.token,bytes(self.pcm)))
        async def shutdown(self):
            if self.closing:return
            self.closing=True;self.invalidate_floor_response();self.overlap_onset_settled.set();gate.resume();self.transition(Ownership.CLOSED,'shutdown')
            jobs=list(self.jobs)
            for job in jobs:job.cancel()
            if jobs:
                if getattr(self,'floor_admission_enabled',False):
                    done,pending=await asyncio.wait(jobs,timeout=5)
                    if pending:
                        for job in pending:job.cancel()
                        self.pipeline_failed.set()
                else:await asyncio.gather(*jobs,return_exceptions=True)
            if getattr(self,'floor_admission_enabled',False):
                unsettled=bool(jobs and pending)
                for close in (self.controller.close,self.endpoint.close):
                    cleanup=asyncio.create_task(close())
                    done,remaining=await asyncio.wait({cleanup},timeout=5)
                    if remaining:
                        cleanup.cancel();unsettled=True
                    else:
                        try:await cleanup
                        except BaseException:unsettled=True
                if self.warm_floor_lane is not None:await self.warm_floor_lane.close()
                acknowledgment=getattr(self,'planning_ack',None)
                if acknowledgment is not None:await acknowledgment.close()
                if unsettled:
                    self.pipeline_failed.set();raise RuntimeError('floor_shutdown_retirement_unsettled')
                return
            await self.controller.close();await self.endpoint.close()
            acknowledgment=getattr(self,'planning_ack',None)
            if acknowledgment is not None:await acknowledgment.close()
        async def cleanup(self):
            try:await self.shutdown()
            finally:
                await self.vad.cleanup();await super().cleanup()
    processor=ConversationProcessor()
    processor.output_transport=transport.output()
    class GenerationFilter(FrameProcessor):
        async def process_frame(self,frame,direction):
            await super().process_frame(frame,direction);g=getattr(frame,'m3_generation',None)
            if g is not None and not processor.controller.current(g):return
            token=getattr(frame,'m3_planning_ack_token',None);acknowledgment=getattr(processor,'planning_ack',None)
            if token is not None and (acknowledgment is None or not acknowledgment.valid(token,g)):return
            reset=getattr(frame,'m3_planning_ack_reset',None)
            if reset is not None and (acknowledgment is None or not acknowledgment.reset_valid(reset,g)):return
            await self.push_frame(frame,direction)
    pipeline=Pipeline([transport.input(),processor,output_channel_adapter(settings.output_channels),GenerationFilter(),transport.output()])
    worker=PipelineWorker(pipeline,params=PipelineParams(audio_in_sample_rate=16000,audio_out_sample_rate=settings.output_rate),idle_timeout_secs=settings.max_seconds,observers=[make_fault_observer(events,processor.pipeline_failed)])
    events.emit('completion_configuration',endpoint='bundled SmartTurnV3.2 CPU',endpoint_deadline_seconds=2,endpoint_minimum_complete_score=.75,score_is_uncalibrated=True,hold_max_total_silence_seconds=3,semantic_segment_limit=4,per_session_ASR_attempt_limit=32,vad_start_secs=.15,vad_stop_secs=.7,overlap_probe_deadline_seconds=8,voice='unchanged local Piper/resident',speech_path='ASR MCP -> text or task MCP -> TTS MCP',max_pcm_bytes=480000,model_audio_logging=False)
    return WorkerRunner(),worker,transport,processor
