"""Preserve the complete bounded PCM prefix across repeated ASR cancellations."""
import base64,time,asyncio
from me3_completion.controller import ConversationController
from me3_m7.controller import WINDOW
from speech_mcp.controller import TurnController
from me3_agent.segments import resolve_segments
class AgentController(ConversationController):
    segment_resolver=staticmethod(resolve_segments)
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self.deadlines={};self.synthesis_budget_seconds=35;original=self.play_audio
        async def playback(pcm,generation,*a,**k):
            deadline=self.deadlines.get(generation)
            if deadline and not deadline['audio_credited'] and not deadline['timer'].expired():
                seconds=len(pcm)/48000
                if not 0<seconds<=45:raise ValueError('invalid_playback_duration')
                deadline['audio_credited']=True
                # Processing still has its original55s bound until actual PCM exists.
                # Credit only this known audio duration plus bounded scheduling/probe grace.
                deadline['timer'].reschedule(deadline['timer'].when()+seconds+10)
                self.events.emit('agent_playback_deadline_credit',generation=generation,known_pcm_seconds=seconds,maximum_pause_grace_seconds=10,processing_budget_seconds=self.turn_deadline)
            return await original(pcm,generation,*a,**k)
        self.play_audio=playback
    def credit_synthesis(self,generation):
        deadline=self.deadlines.get(generation)
        if not deadline or deadline.get('synthesis_credited') or deadline['timer'].expired():return
        deadline['synthesis_credited']=True
        before=deadline['timer'].when();after=max(before,time.monotonic()+self.synthesis_budget_seconds)
        deadline['timer'].reschedule(after)
        self.events.emit('agent_synthesis_deadline_credit',generation=generation,synthesis_stage_budget_seconds=self.synthesis_budget_seconds,extension_seconds=after-before,one_credit_per_response=True)
    async def speak(self,text,generation,*args,**kwargs):
        self.credit_synthesis(generation)
        return await super().speak(text,generation,*args,**kwargs)
    def credit_overlap(self,generation):
        deadline=self.deadlines.get(generation)
        if not deadline or deadline.get('overlap_credited') or deadline['timer'].expired():return
        deadline['overlap_credited']=True;deadline['timer'].reschedule(deadline['timer'].when()+35)
        self.events.emit('agent_overlap_deadline_credit',generation=generation,maximum_probe_and_cleanup_seconds=35,one_credit_per_response=True)
    async def turn(self,pcm,generation):
        outcome='completed';self.events.emit('core_turn_started',generation=generation,deadline_seconds=self.turn_deadline,playback_budget=getattr(self,'playback_budget_description','separate35s synthesis allowance, known PCM duration+10s grace and one35s overlap credit, maximum180s total'))
        try:
            async with asyncio.timeout(self.turn_deadline) as timer:
                self.deadlines[generation]={'timer':timer,'audio_credited':False}
                await TurnController.turn(self,pcm,generation)
        except asyncio.CancelledError:outcome='canceled';raise
        except TimeoutError:
            outcome='timeout';self.events.emit('core_turn_deadline',generation=generation,automatic_retry=False)
            self.deadlines.pop(generation,None)
            if self.current(generation):
                self.abandon_unconfirmed(generation)
                invalidate=getattr(self,'invalidate_timed_out_output',None)
                if invalidate is not None:
                    try:
                        async with asyncio.timeout(2):
                            if not await invalidate(generation):return
                    except Exception as exc:
                        self.events.emit('agent_expired_output_cleanup_failed',generation=generation,error_type=type(exc).__name__,notice_suppressed=True)
                        if hasattr(self.clients,'mark_failure'):self.clients.mark_failure('output_cleanup',None)
                        return
                if not self.current(generation):return
                try:
                    async with asyncio.timeout(10):await self.speak('That request took too long. Please say your next request.',generation,'normal',error_notice=True)
                except TimeoutError:self.events.emit('core_timeout_notice_failed',generation=generation)
        except BaseException:outcome='error';raise
        finally:
            self.deadlines.pop(generation,None);self.events.emit('core_turn_settled',generation=generation,outcome=outcome,observed_ns=time.monotonic_ns())
    async def recognize(self,generation,**arguments):
        slot=self.slot
        candidate=slot.get('candidate') if slot and slot['generation']==generation else None
        if candidate and candidate.get('pcm') and not candidate.get('held_text') and not candidate.get('prefix_lost') and time.monotonic()-candidate['submitted']<=WINDOW:
            merged=candidate['pcm']+slot['pcm']
            if len(merged)>getattr(self,'max_pcm_bytes',480000):
                slot['stage']='input_limit'
                self.events.emit('agent_retained_input_limit',generation=generation,byte_count=len(merged),action='clarify_no_partial_execution')
                return await self.clarify(generation)
            # Retain the whole actual PCM prefix in the current slot BEFORE await.
            # Any later interruption now saves that complete prefix, not just clip two.
            # No silence, words, or connector audio are invented at the clip boundary.
            slot['pcm']=merged;slot['candidate']=None
            arguments['pcm_b64']=base64.b64encode(merged).decode()
            arguments.pop('previous_pcm_b64',None);arguments.pop('previous_generation',None)
            self.events.emit('agent_audio_prefix_merged',generation=generation,previous_bytes=len(candidate['pcm']),current_bytes=len(merged)-len(candidate['pcm']),total_bytes=len(merged),boundary='captured clips concatenated; unrecorded inter-clip gap omitted',raw_audio_retained=False)
        return await super().recognize(generation,**arguments)
