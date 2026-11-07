"""Candidate complete-answer playout; each student MCP synthesis keeps its45s bound."""
import asyncio,math
from contextlib import asynccontextmanager
from me3_assistant.speech import AssistantController,response_parts
from me3_agent.controller import AgentController
from onit_candidate.presentation import spoken_text,speech_parts
from onit_candidate.protocol import MAX_RESPONSE_CHARS
from speech_mcp.contracts import decode_pcm,MAX_TTS_SECONDS

class CandidateSpeech(AssistantController):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self._greeting_generation=None;self._greeting_prerolled=False;original=self.play_audio
        async def playback(pcm,generation,*a,**kw):
            if self._greeting_generation==generation and not self._greeting_prerolled and self.current(generation):
                self._greeting_prerolled=True;original_bytes=len(pcm);pcm=bytes(12000)+pcm
                self.events.emit('onit_greeting_output_preroll',generation=generation,milliseconds=250,original_PCM_bytes=original_bytes,total_PCM_bytes=len(pcm),extra_synthesis_calls=0,physical_fix_verified=False)
            # Existing owner credits this actual padded duration, and the final
            # callback/AEC sees the same stream. Never remove original samples.
            return await original(pcm,generation,*a,**kw)
        self.play_audio=playback
    playback_budget_description="ASR/voice processing55s; native Onit planning has its configured task deadline and per-request/RPC limits; actual observed provider waits within the outer configured session, synthesis stage35s, each PCM<=45s, actual known segmented PCM duration plus one10s grace and one35s overlap credit; outer configured session deadline remains binding, no provider/model retry."
    capacity_wait_ceiling=60
    @asynccontextmanager
    async def planning_scope(self,generation,seconds):
        # The native task loop already has bounded model/RPC counts, HTTP/RPC
        # timeouts and a configured absolute deadline. Do not spend the voice
        # ASR/TTS processing allowance again across its entire multistep loop.
        # The outer session supervisor and user interruption remain binding.
        deadline=self.deadlines.get(generation)
        if type(seconds) not in (int,float) or not math.isfinite(seconds) or not 0<seconds<=3600:raise ValueError('invalid_native_planning_deadline')
        if not self.current(generation) or not deadline or deadline['timer'].expired():raise asyncio.CancelledError()
        if deadline.get('planning_owned'):raise ValueError('native_planning_scope_already_active')
        timer=deadline['timer'];loop=asyncio.get_running_loop();remaining=max(0,timer.when()-loop.time())
        deadline['planning_owned']=True;deadline['planning_hard_end']=loop.time()+seconds;timer.reschedule(deadline['planning_hard_end'])
        self.events.emit('onit_voice_planning_deadline_enter',generation=generation,task_budget_seconds=seconds,voice_processing_remaining_seconds=remaining,per_request_timeouts_unchanged=True,outer_session_binding=True,automatic_retry=False)
        try:yield
        finally:
            deadline['planning_owned']=False
            # Never revive an expired timer or transfer old credits to a new
            # generation. Only this owner's unused voice allowance resumes.
            restored=self.current(generation) and self.deadlines.get(generation) is deadline and not timer.expired()
            if restored:timer.reschedule(loop.time()+remaining)
            self.events.emit('onit_voice_planning_deadline_exit',generation=generation,voice_allowance_restored=restored,automatic_retry=False)
    def credit_provider_wait(self,generation,seconds):
        from me3_assistant.admission import AdmissionDenied
        deadline=self.deadlines.get(generation)
        if type(seconds) not in (int,float) or not math.isfinite(seconds) or not 0<seconds<=60 or not self.current(generation) or not deadline or deadline['timer'].expired():raise AdmissionDenied('capacity_wait_budget')
        spent=deadline.get('provider_wait_credited',0)
        if spent+seconds>self.capacity_wait_ceiling:raise AdmissionDenied('capacity_wait_budget')
        deadline['provider_wait_credited']=spent+seconds
        if not deadline.get('planning_owned'):deadline['timer'].reschedule(deadline['timer'].when()+seconds)
        else:self.events.emit('onit_planning_capacity_wait_covered',generation=generation,seconds=seconds,task_deadline_extended=False)
        self.events.emit('assistant_capacity_deadline_credit',generation=generation,seconds=seconds,total_credited_seconds=spent+seconds,maximum_total_seconds=self.capacity_wait_ceiling,wire_attempts=0,automatic_retry=False)
    async def background_notice_speech(self,text,generation,owned):
        """One actual normal-client synthesis; no normal-answer/retained hooks."""
        if not isinstance(text,str) or not 1<=len(text)<=180 or not callable(owned):raise ValueError('bounded_background_notice_required')
        if asyncio.current_task() is not self.task or not self.current(generation) or not owned():raise asyncio.CancelledError()
        self.real_clients.require_tts_capacity(1)
        async with asyncio.timeout(35):
            data=await self.clients.call('tts',generation,text=text,style='normal',language='en')
            if not self.current(generation) or not owned():raise asyncio.CancelledError()
            pcm=decode_pcm(data['pcm_b64'],24000,8)
            await self.play_audio(pcm,generation,'background_notice')
            if not self.current(generation) or not owned():raise asyncio.CancelledError()
        self.events.emit('background_notice_PCM_submitted',generation=generation,byte_count=len(pcm),acoustic_completion_measured=False)
        return True
    async def task_ack_synthesize(self,text,generation):
        if asyncio.current_task() is not self.task or not self.current(generation) or self.provider.pending:raise asyncio.CancelledError()
        self.real_clients.require_tts_capacity(2) # cue plus one real answer; ordinary call owns charges.
        self.credit_synthesis(generation)
        data=await self.clients.call('tts',generation,text=text,style='normal',language='en')
        if not self.current(generation):raise asyncio.CancelledError()
        pcm=decode_pcm(data['pcm_b64'],24000,4)
        self.events.emit('task_ack_PCM_ready',generation=generation,byte_count=len(pcm),substantive_answer=False)
        return pcm
    async def task_ack_play(self,pcm,generation):
        if asyncio.current_task() is not self.task or not self.current(generation) or self.provider.pending:raise asyncio.CancelledError()
        await self.play_audio(pcm,generation,'task_ack')
        if not self.current(generation):raise asyncio.CancelledError()
        self.events.emit('task_ack_submitted',generation=generation,substantive_answer=False,acoustic_completion_measured=False)
    async def validated_synthesize(self,text,language,generation):
        # Uses the exact ordinary speech client/counters and PCM bounds.
        if not self.current(generation):raise asyncio.CancelledError()
        if language not in getattr(self.real_clients,'speech_languages',('en',)):raise ValueError('unsupported_speech_language')
        self.real_clients.require_tts_capacity(1);self.credit_synthesis(generation)
        self.spoken.add(generation)
        data=await self.clients.call('tts',generation,text=text,style='normal',language=language)
        if not self.current(generation):raise asyncio.CancelledError()
        return decode_pcm(data['pcm_b64'],24000,MAX_TTS_SECONDS)
    async def validated_play(self,pcm,generation,index):
        if not self.current(generation):raise asyncio.CancelledError()
        deadline=self.deadlines.get(generation)
        if index and deadline and deadline['audio_credited'] and not deadline['timer'].expired():
            deadline['timer'].reschedule(deadline['timer'].when()+len(pcm)/48000)
            self.events.emit('assistant_remaining_playback_credit',generation=generation,seconds=len(pcm)/48000,no_additional_grace=True,validated_units=True)
        if index==0:self.events.emit('response_audio_ready',generation=generation,byte_count=len(pcm),partial=True,greeting=False,error_notice=False,validated_units=True)
        # Must be awaited inline on controller.task: actual pipeline.play uses
        # that identity to establish first PCM and semantic floor ownership.
        await self.play_audio(pcm,generation)
        if not self.current(generation):raise asyncio.CancelledError()
    async def _speak_response(self,text,generation,style,**flags):
        if not self.current(generation):return False
        if not isinstance(text,str) or not text.strip() or len(text)>MAX_RESPONSE_CHARS:
            self.events.emit('turn_error',generation=generation,stage='tts_or_playout',error_type='ValueError',code='speech_text_bound')
            return False
        raw_segments=flags.pop('speech_segments',None);already_projected=flags.pop('speech_projected',False)
        if flags.get('greeting') or flags.get('error_notice'):raw_segments=None
        if raw_segments is not None:
            from onit_candidate.speech_presentation import languages,validate_segments,project
            available=languages(getattr(self.real_clients,'speech_languages',('en',)))
            validate_segments(raw_segments,available=available,text=text)
            projected=project(raw_segments,available=available,already_projected=already_projected)
            parts=[];part_languages=[]
            for row in projected:
                chunks=speech_parts(row['text']);parts.extend(chunks);part_languages.extend([row['language']]*len(chunks))
            text=''.join(parts)
        else:
            text=spoken_text(text);parts=None;part_languages=None
        if flags.get('greeting'):
            self._greeting_generation=generation
            try:return await AgentController.speak(self,text,generation,style,**flags)
            finally:self._greeting_generation=None
        if flags.get('error_notice'):
            return await AgentController.speak(self,text,generation,style,**flags)
        if parts is None:parts=speech_parts(text)
        if len(parts)==1 and part_languages is None:return await AgentController.speak(self,text,generation,style,**flags)
        pending=None;partial=None;complete=False;total_bytes=0
        self.credit_synthesis(generation);self.spoken.add(generation)
        if len(self.spoken)>32:self.spoken={g for g in self.spoken if g>=self.clock.current-16}
        async def synthesize(part,index):
            if not self.current(generation):raise asyncio.CancelledError()
            data=await self.clients.call('tts',generation,text=part,style=style,**(dict(language=part_languages[index]) if part_languages is not None else {}))
            if not self.current(generation):raise asyncio.CancelledError()
            pcm=decode_pcm(data['pcm_b64'],24000,MAX_TTS_SECONDS)
            self.events.emit('assistant_speech_part_ready',generation=generation,index=index,parts=len(parts),byte_count=len(pcm))
            return pcm
        try:
            # Fit the complete bounded response before dispatching its first call.
            # Every call is still reserved by the ordinary speech client; no refund/cache bypass.
            self.real_clients.require_tts_capacity(len(parts))
            self.events.emit('assistant_speech_preparing',generation=generation,part_index=0,parts=len(parts),complete_reply_pending=True)
            pcm=await synthesize(parts[0],0)
            for index,part in enumerate(parts):
                if not self.current(generation):return False
                total_bytes+=len(pcm)
                # Only actual PCM earns time. The outer session supervisor still
                # stops this response at the configured session deadline.
                # AgentController credits the first duration plus one grace
                # window. Later parts add only their actual known PCM duration.
                deadline=self.deadlines.get(generation)
                if index and deadline and deadline['audio_credited'] and not deadline['timer'].expired():
                    deadline['timer'].reschedule(deadline['timer'].when()+len(pcm)/48000)
                    self.events.emit('assistant_remaining_playback_credit',generation=generation,seconds=len(pcm)/48000,total_known_pcm_seconds=total_bytes/48000,no_additional_grace=True)
                if index+1<len(parts):
                    pending=asyncio.create_task(synthesize(parts[index+1],index+1))
                if index==0:
                    self.events.emit('response_audio_ready',generation=generation,byte_count=len(pcm),partial=True,greeting=False,error_notice=False)
                await self.play_audio(pcm,generation)
                if not self.current(generation):return False
                submitted=''.join(parts[:index+1])
                note=getattr(self,'note_reply_submission',None)
                if note:note(generation,submitted)
                if index+1<len(parts):
                    if partial is None:
                        partial={'type':'model_output','partial_output':True,'content':[{'type':'text','text':submitted}]}
                        self.history.append(partial)
                    else:partial['content'][0]['text']=submitted
                    self.events.emit('assistant_speech_prefix_submitted',generation=generation,part_index=index,chars=len(submitted),acoustic_completion_measured=False)
                    if not pending.done():self.events.emit('assistant_speech_preparing',generation=generation,part_index=index+1,parts=len(parts),complete_reply_pending=True)
                    pcm=await pending;pending=None
            complete=True
            self.events.emit('response_complete',generation=generation,greeting=False,error_notice=False,parts=len(parts))
            return True
        except asyncio.CancelledError:
            self.events.emit('response_task_cancelled',generation=generation);raise
        except Exception as exc:
            self.events.emit('turn_error',generation=generation,stage='tts_or_playout',error_type=type(exc).__name__,code=getattr(exc,'code',None))
            return False
        finally:
            if pending is not None:
                pending.cancel();await asyncio.gather(pending,return_exceptions=True)
            if complete and partial is not None:
                # The inherited turn method appends the one complete answer atomically.
                self.history=[row for row in self.history if row is not partial]
