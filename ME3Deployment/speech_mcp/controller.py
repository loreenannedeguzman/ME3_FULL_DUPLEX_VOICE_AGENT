"""Generation-owned, cancelable speech turn orchestration; no device dependency."""
import asyncio,base64
from speech_mcp.contracts import GenerationClock,decode_pcm,MAX_TTS_SECONDS

class TurnController:
    def __init__(self,clients,provider,events,play_audio,max_turns=8):
        self.clients=clients;self.provider=provider;self.events=events;self.play_audio=play_audio
        self.clock=GenerationClock();self.history=[];self.task=None;self.tasks=set();self.turns=0;self.max_turns=max_turns;self.closed=False;self.pending_history_generation=None
    def spawn(self,coroutine):
        if self.closed:
            coroutine.close();return
        self.task=asyncio.create_task(coroutine);self.tasks.add(self.task)
        self.task.add_done_callback(self.tasks.discard)
    def close_pending_history(self,generation,outcome):
        if self.pending_history_generation!=generation:return
        if outcome not in {'cancelled','failed'}:raise ValueError('invalid_context_outcome')
        self.pending_history_generation=None
        instruction=('Previous response was cancelled. Do not resume that task; answer only the latest user request.' if outcome=='cancelled' else 'Previous request failed. Do not automatically retry it; answer only the latest user request.')
        self.history.append({'type':'model_output','control_state':outcome,'content':[{'type':'text','text':'[UNSPOKEN CONTROL STATE] '+instruction}]})
        self.history=self.history[-16:]
        self.events.emit('context_turn_closed',generation=generation,outcome=outcome,spoken=False)
    def interrupt(self):
        previous=self.clock.current;self.close_pending_history(previous,'cancelled');generation=self.clock.advance()
        pending=self.task is not None and not self.task.done()
        if pending:self.task.cancel()
        self.events.emit('turn_cancelled',generation=previous,replacement_generation=generation,pending_work=pending)
        return generation
    def current(self,generation):
        valid=self.clock.accepts(generation)
        if not valid:self.events.emit('stale_generation_rejected',generation=generation,current_generation=self.clock.current)
        return valid
    def greet(self):self.spawn(self.speak('I am ready. Please say hello.',self.clock.current,'normal',greeting=True))
    def submit(self,pcm,generation):
        if self.closed:
            self.events.emit('turn_rejected_closed',generation=generation);return
        if not self.current(generation):return
        if self.turns>=self.max_turns:
            self.events.emit('turn_budget_reached',max_turns=self.max_turns);return
        self.turns+=1
        self.spawn(self.turn(pcm,generation))
    async def speak(self,text,generation,style,greeting=False,error_notice=False):
        try:
            data=await self.clients.call('tts',generation,text=text,style=style)
            if not self.current(generation):return False
            pcm=decode_pcm(data['pcm_b64'],24000,MAX_TTS_SECONDS)
            self.events.emit('response_audio_ready',generation=generation,byte_count=len(pcm),greeting=greeting,error_notice=error_notice)
            await self.play_audio(pcm,generation)
            if self.current(generation):
                self.events.emit('response_complete',generation=generation,greeting=greeting,error_notice=error_notice)
                return True
            return False
        except asyncio.CancelledError:
            self.events.emit('response_task_cancelled',generation=generation);raise
        except Exception as exc:
            self.events.emit('turn_error',generation=generation,stage='tts_or_playout',error_type=type(exc).__name__,code=getattr(exc,'code',None))
            return False
    async def turn(self,pcm,generation):
        stage='asr'
        try:
            data=await self.clients.call('asr',generation,pcm_b64=base64.b64encode(pcm).decode(),sample_rate=16000)
            if not self.current(generation):return
            text=data.get('text','').strip()
            if not text:
                handled=getattr(self,'consume_handled_recognition',None)
                # Only controller-owned state may classify an already spoken
                # clarification. Provider payload flags cannot suppress a fault.
                if callable(handled) and handled(generation):
                    self.events.emit('assistant_recognition_handled',generation=generation,reason='controller_clarification')
                else:self.events.emit('empty_transcription',generation=generation)
                return
            if len(text)>getattr(self,'max_transcript_chars',1000):raise ValueError('transcript_size_limit')
            self.events.emit('transcription_ready',generation=generation,char_count=len(text))
            self.history.append({'type':'user_input','content':[{'type':'text','text':text}]})
            self.history=self.history[-16:];self.pending_history_generation=generation
            stage='reasoning';self.events.emit('reasoning_start',generation=generation)
            answer=await self.provider.answer(list(self.history))
            if not self.current(generation):return
            self.events.emit('reasoning_end',generation=generation,char_count=len(answer))
            stage='tts_or_playout'
            played=await self.speak(answer,generation,'slow' if 'count' in text.lower() else 'normal')
            if played and self.current(generation):
                self.history.append({'type':'model_output','content':[{'type':'text','text':answer}]})
                self.pending_history_generation=None
            else:self.close_pending_history(generation,'failed')
        except asyncio.CancelledError:
            self.close_pending_history(generation,'cancelled')
            self.events.emit('turn_task_cancelled',generation=generation,stage=stage);raise
        except Exception as exc:
            self.close_pending_history(generation,'failed')
            self.events.emit('turn_error',generation=generation,stage=stage,error_type=type(exc).__name__,code=getattr(exc,'code',None))
            if getattr(exc,'code',None)==429:
                if hasattr(self.clients,'limit_reached'):self.clients.limit_reached.set()
                return
            if hasattr(self.clients,'mark_failure'):
                self.clients.mark_failure(stage,getattr(exc,'code',None));return
            if stage in {'asr','reasoning'} and self.current(generation):
                await self.speak('I could not complete that request. Please try again.',generation,'normal',error_notice=True)
    async def close(self):
        if not self.closed:
            self.closed=True;self.close_pending_history(self.clock.current,'cancelled');self.clock.advance()
        tasks=list(self.tasks)
        for task in tasks:task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
