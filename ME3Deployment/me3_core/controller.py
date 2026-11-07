"""Bound actual work, settle every turn, and make empty recognition audible once."""
import asyncio,time
from me3_m7.controller import M7TurnController
class CoreTurnController(M7TurnController):
    def __init__(self,*args,turn_deadline_seconds=55,**kwargs):
        super().__init__(*args,**kwargs)
        if not 1<=turn_deadline_seconds<=60:raise ValueError('invalid_turn_deadline')
        self.turn_deadline=turn_deadline_seconds;self.spoken=set()
    async def speak(self,text,generation,style,**kwargs):
        if not self.current(generation):return False
        self.spoken.add(generation)
        if len(self.spoken)>32:self.spoken={g for g in self.spoken if g>=self.clock.current-16}
        return await super().speak(text,generation,style,**kwargs)
    async def recognize(self,generation,**arguments):
        self.spoken.discard(generation)
        data=await super().recognize(generation,**arguments)
        if not data.get('text','').strip() and self.current(generation) and generation not in self.spoken:
            self.events.emit('core_empty_speech_clarification',generation=generation)
            return await self.clarify(generation)
        return data
    async def turn(self,pcm,generation):
        outcome='completed'
        self.events.emit('core_turn_started',generation=generation,deadline_seconds=self.turn_deadline)
        try:
            async with asyncio.timeout(self.turn_deadline):await super().turn(pcm,generation)
        except asyncio.CancelledError:outcome='canceled';raise
        except TimeoutError:
            outcome='timeout';self.events.emit('core_turn_deadline',generation=generation,automatic_retry=False)
            if self.current(generation):
                self.abandon_unconfirmed(generation)
                try:
                    async with asyncio.timeout(10):await self.speak('That request took too long. Please say your next request.',generation,'normal',error_notice=True)
                except TimeoutError:self.events.emit('core_timeout_notice_failed',generation=generation)
        except BaseException:outcome='error';raise
        finally:self.events.emit('core_turn_settled',generation=generation,outcome=outcome,observed_ns=time.monotonic_ns())
