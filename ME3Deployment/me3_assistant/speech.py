"""Bounded ordinary syntheses, with one lookahead and generation-owned playback."""
import asyncio,re,math
from me3_agent.controller import AgentController
from speech_mcp.contracts import decode_pcm,MAX_TTS_SECONDS

def response_parts(text):
    # A short numeric list can be long when spoken. Preserve every byte and
    # split only bare comma-separated integers at existing item boundaries.
    # Ordinary prose, decimals, quantities and larger lists keep their old path.
    if re.fullmatch(r'\s*[0-9]{1,4}(?:,\s+[0-9]{1,4}){7,19}[.!]?\s*',text):
        ends=[m.end() for m in re.finditer(r',\s+',text)]
        cuts=[0]+[ends[i] for i in range(4,len(ends),5)]+[len(text)]
        return [text[a:b] for a,b in zip(cuts,cuts[1:])]
    if len(text)<=72:return [text]
    # Prefer a complete short sentence/clause. Never cut a word, quantity or text.
    stops=[m.end() for m in re.finditer(r'[.!?;,:](?=\s)',text[:73]) if m.end()>=35]
    cut=stops[0] if stops else text.rfind(' ',35,73)
    if cut<35:return [text]
    parts=[text[:cut]];remaining=text[cut:]
    while len(remaining)>160:
        stops=[m.end() for m in re.finditer(r'[.!?;,:](?=\s)',remaining[:161]) if m.end()>=35 and not re.search(r'(?:^|\s)[0-9]+\.$',remaining[:m.end()])]
        end=stops[-1] if stops else remaining.rfind(' ',35,161)
        if end<35:
            # Keep an unbroken word/URL/quantity intact, never omit or rewrite it.
            end=remaining.find(' ',160)
            if end<0:break
        parts.append(remaining[:end]);remaining=remaining[end:]
    if remaining:parts.append(remaining)
    return parts

class AssistantController(AgentController):
    def segment_resolver(self,previous,current):
        # Retained unfinished audio/text still uses the conservative split
        # policy. A separately captured follow-up is interpreted in the normal
        # reasoner's shared conversation context, including conjunctions.
        from me3_agent.segments import resolve_segments
        self._normal_recognition=(self.clock.current,current)
        decision,resolved=resolve_segments(previous,current)
        if decision=='ambiguous' and not previous and isinstance(current,str) and current.strip():
            return 'complete',current
        return decision,resolved

    async def recognize(self,generation,**arguments):
        self._normal_handled_generation=None;self._normal_recognition=None
        return await super().recognize(generation,**arguments)

    def consume_handled_recognition(self,generation):
        handled=getattr(self,'_normal_handled_generation',None)==generation and self.current(generation)
        if handled:self._normal_handled_generation=None
        return handled

    async def clarify(self,generation):
        if not self.current(generation):return {'text':''}
        self.abandon_unconfirmed(generation)
        recognized=getattr(self,'_normal_recognition',None)
        known=bool(recognized and recognized[0]==generation and isinstance(recognized[1],str) and recognized[1].strip())
        if known and getattr(self,'_clarification_history_generation',None)!=generation:
            self.history.append({'type':'user_input','clarification_requested':True,'content':[{'type':'text','text':recognized[1]}]})
            self.history=self.history[-16:];self.pending_history_generation=generation;self._clarification_history_generation=generation
        prompt="Could you clarify what you'd like me to do?"
        # Missing actual words have no user row to anchor; do not overwrite the
        # preceding reply. Genuine empty ASR retains empty_transcription below.
        played=await self.speak(prompt,generation,'normal',error_notice=not known)
        if known and played and self.current(generation):
            self.history.append({'type':'model_output','content':[{'type':'text','text':prompt}]})
            self.history=self.history[-16:];self.pending_history_generation=None
            self._normal_handled_generation=generation
            self.events.emit('assistant_clarification_delivered',generation=generation,recognized_chars=len(recognized[1]),input_retained_in_memory=True,action_started=False,transcript_retained=False)
        elif known and self.current(generation):self.close_pending_history(generation,'failed')
        return {'text':''}

    playback_budget_description='Normal55s processing, at most60s observed provider-reset waits per generation,35s synthesis stage, actual PCM duration+10s grace and one35s overlap credit; bounded upper total240s'
    def credit_provider_wait(self,generation,seconds):
        from me3_assistant.admission import AdmissionDenied
        deadline=self.deadlines.get(generation)
        if (type(seconds) not in (int,float) or not math.isfinite(seconds) or not 0<seconds<=60
            or not self.current(generation) or not deadline or deadline['timer'].expired()):
            raise AdmissionDenied('capacity_wait_budget')
        spent=deadline.get('provider_wait_credited',0)
        if spent+seconds>60:raise AdmissionDenied('capacity_wait_budget')
        deadline['provider_wait_credited']=spent+seconds
        deadline['timer'].reschedule(deadline['timer'].when()+seconds)
        self.events.emit('assistant_capacity_deadline_credit',generation=generation,seconds=seconds,total_credited_seconds=spent+seconds,maximum_total_seconds=60,wire_attempts=0,automatic_retry=False)

    async def speak(self,text,generation,style,**flags):
        # GUI-only projection: never persist text through metadata events or
        # put an intended but interrupted reply into model/action history.
        display=getattr(self,'display_reply',None)
        projected=display is not None and not flags.get('greeting') and not flags.get('error_notice') and self.current(generation)
        state='failed'
        if projected:display(generation,text,'preparing')
        try:
            played=await self._speak_response(text,generation,style,**flags)
            state='complete' if played else ('interrupted' if not self.current(generation) else 'failed')
            return played
        except asyncio.CancelledError:
            state='interrupted';raise
        finally:
            if projected:display(generation,text,state)

    async def _speak_response(self,text,generation,style,**flags):
        if flags.get('greeting') or flags.get('error_notice'):
            return await super().speak(text,generation,style,**flags)
        if not self.current(generation):return False
        from me3_assistant.spoken_text import spoken_text
        text=spoken_text(text)
        parts=response_parts(text)
        if len(parts)==1:return await super().speak(text,generation,style,**flags)
        pending=None;partial=None;complete=False;total_bytes=0
        self.credit_synthesis(generation);self.spoken.add(generation)
        if len(self.spoken)>32:self.spoken={g for g in self.spoken if g>=self.clock.current-16}
        async def synthesize(part,index):
            if not self.current(generation):raise asyncio.CancelledError()
            data=await self.clients.call('tts',generation,text=part,style=style)
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
                if total_bytes>MAX_TTS_SECONDS*48000:raise ValueError('combined_speech_duration_limit')
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
                if index+1<len(parts):
                    submitted=''.join(parts[:index+1])
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
