"""Adopt one validated ASR probe without duplicate recognition or stale identity."""
import asyncio,base64,time
from me3_core.controller import CoreTurnController
from me3_m7.controller import WINDOW,fragment_shape
from me3_m7.client import RecoverableASRTimeout,RecoverableASRTransportError
from me3_completion.policy import resolve_segments
class PrefetchClient:
    def __init__(self,client):self.client=client;self.ready={}
    def __getattr__(self,name):return getattr(self.client,name)
    async def call(self,role,generation,**arguments):
        if role=='asr' and generation in self.ready:return self.ready.pop(generation)
        return await self.client.call(role,generation,**arguments)
class ConversationController(CoreTurnController):
    def __init__(self,clients,*args,**kwargs):
        self.prefetch=PrefetchClient(clients);super().__init__(self.prefetch,*args,**kwargs)
        self.asr_submissions=0;self.probe_submissions=0;self.segment_attempts=0;self.floor_cued=False;self.listener_cue=None;self.listener_ack_count=0
    def reserve_overlap_probe(self):
        if self.asr_submissions+self.probe_submissions>=self.max_turns*4:
            self.events.emit('turn_budget_reached',max_turns=self.max_turns,reason='bounded_ASR_attempts');return False
        self.probe_submissions+=1;return True
    def submit(self,pcm,generation):
        if self.closed or not self.current(generation):return
        candidate=self.saved;self.saved=None
        continuation=bool(candidate and time.monotonic()-candidate['submitted']<=WINDOW)
        attempts=self.segment_attempts+1 if continuation else 1
        if self.asr_submissions+self.probe_submissions>=self.max_turns*4 or not continuation and self.turns>=self.max_turns:
            self.events.emit('turn_budget_reached',max_turns=self.max_turns,reason='bounded_turn_or_ASR_attempts');return
        if attempts>4:
            self.slot=None;self.events.emit('completion_segment_limit',generation=generation,maximum=4,automatic_retry=False)
            self.spawn(self.clarify(generation));return
        self.segment_attempts=attempts;self.asr_submissions+=1
        if not continuation:self.turns+=1
        self.slot={'generation':generation,'pcm':pcm,'stage':'asr','submitted':time.monotonic(),'candidate':candidate if continuation else None}
        self.events.emit('completion_semantic_turn',generation=generation,continuation=continuation,semantic_turn_count=self.turns,asr_submissions=self.asr_submissions,segment_attempts=attempts)
        self.spawn(self.turn(pcm,generation))
    async def recognize(self,generation,**arguments):
        slot=self.slot;candidate=slot.get('candidate') if slot and slot['generation']==generation else None
        previous_text=candidate.get('text') if candidate else None
        if candidate and candidate.get('pcm'):
            if time.monotonic()-candidate['submitted']<=WINDOW and len(candidate['pcm'])+len(slot['pcm'])<=getattr(self,'max_pcm_bytes',480000):
                arguments.update(previous_pcm_b64=base64.b64encode(candidate['pcm']).decode(),previous_generation=candidate['generation'])
                self.events.emit('m7_separate_clip_request',generation=generation,previous_generation=candidate['generation'],previous_bytes=len(candidate['pcm']))
            else:candidate=None
        try:data=await self.real_clients.call('asr',generation,**arguments)
        except (RecoverableASRTimeout,RecoverableASRTransportError) as exc:
            if slot and slot.get('generation')==generation:slot['stage']='failed'
            transport=isinstance(exc,RecoverableASRTransportError)
            if transport and self.current(generation):
                self.saved=None
                if slot and slot.get('generation')==generation:slot.update(pcm=b'',candidate=None,recognized=None)
                self.prefetch.ready.pop(generation,None)
            if self.current(generation):
                self.abandon_unconfirmed(generation);self.events.emit('m7_transport_notice' if transport else 'm7_timeout_notice',generation=generation)
                prompt=('The speech connection was interrupted. Please say your request again.' if transport else 'That speech request timed out. Please try one complete request.')
                played=await self.speak(prompt,generation,'normal',error_notice=True)
                # This delivered controller notice handled the missing transcript;
                # do not relabel it as an empty provider transcript or retain audio.
                if played and self.current(generation):self._normal_handled_generation=generation
            return {'text':''}
        if not self.current(generation):return data
        if arguments.get('previous_pcm_b64'):previous_text=data.get('previous_text')
        text=data['text']
        # Carry already recognized context while a separate later clip is canceled.
        # Raw older clips beyond the two-clip contract are never silently dropped.
        if candidate and candidate.get('held_text') and previous_text:
            prior_decision,joined=getattr(self,'segment_resolver',resolve_segments)(candidate['held_text'],previous_text)
            if prior_decision=='ambiguous':
                slot['stage']='ambiguous';return await self.clarify(generation)
            previous_text=joined
            self.events.emit('completion_known_prefix_carried',generation=generation,recognized_chars=len(previous_text),transcript_retained=False)
        if candidate and candidate.get('prefix_lost'):
            import re
            if not re.match(r'^(?:actually|instead|stop)[,.:! ]+',text.strip(),re.I):
                slot['stage']='ambiguous';self.events.emit('completion_unrecoverable_prefix',generation=generation,action='clarify_no_execution');return await self.clarify(generation)
        decision,resolved=getattr(self,'segment_resolver',resolve_segments)(previous_text,text)
        self.events.emit('completion_segment_decision',generation=generation,decision=decision,previous_chars=len(previous_text or ''),current_chars=len(text),fragment_shape=fragment_shape(text),recognized_words_only=True,transcript_retained=False)
        if not text:
            slot['stage']='empty';self.events.emit('core_empty_speech_clarification',generation=generation);return await self.clarify(generation)
        if decision=='ambiguous':slot['stage']='ambiguous';self.floor_cued=False;return await self.clarify(generation)
        if decision=='incomplete':
            slot.update(stage='incomplete',recognized=resolved);self.abandon_unconfirmed(generation)
            self.events.emit('completion_semantic_floor_held',generation=generation,window_seconds=6)
            if self.listener_cue is not None and not self.floor_cued:
                self.floor_cued=True;await self.listener_cue(generation)
            await asyncio.sleep(6)
            if self.current(generation):slot['stage']='expired';return await self.clarify(generation)
            return {'text':''}
        self.floor_cued=False;slot['stage']='reasoning';data['text']=resolved;return data
    def submit_recognized(self,pcm,generation,data):
        if self.closed or not self.current(generation):return
        if self.turns>=self.max_turns:
            self.events.emit('turn_budget_reached',max_turns=self.max_turns);return
        if data.get('generation')!=generation or not isinstance(data.get('request_id'),str) or not isinstance(data.get('text'),str):raise ValueError('invalid_prefetched_identity')
        self.prefetch.ready[generation]=dict(data)
        self.events.emit('completion_asr_probe_adopted',generation=generation,request_id=data['request_id'],duplicate_provider_request=False)
        self.submit(pcm,generation)
    def interrupt(self):
        slot=self.slot
        candidate=slot.get('candidate') if slot and slot.get('stage')=='asr' else None
        held_text=(candidate.get('text') or candidate.get('held_text')) if candidate else None
        prefix_lost=bool(candidate and (candidate.get('prefix_lost') or candidate.get('pcm')))
        self.prefetch.ready.clear();generation=super().interrupt()
        if self.saved is not None and candidate:
            self.saved.update(held_text=held_text,prefix_lost=prefix_lost)
        return generation
    async def close(self):
        self.prefetch.ready.clear();await super().close()
