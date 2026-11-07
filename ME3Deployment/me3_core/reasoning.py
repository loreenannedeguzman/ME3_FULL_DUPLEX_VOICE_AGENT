"""Same accepted text model/prompt, per-request wire guard and metadata scope."""
import asyncio,time
from me3_core.pool import ScopedGoogle,SCOPE,RequestScope,dispatch
class CoreReasoning:
    def __init__(self,events):self.events=events;self.traced=ScopedGoogle(dispatch);self.lock=asyncio.Lock()
    async def answer(self,history):
        async with asyncio.timeout(3):await self.lock.acquire()
        generation=getattr(self.events,'current_generation',None)
        scope=RequestScope(lambda event,**kw:self.events.emit('core_text_'+event,generation=generation,**kw));token=SCOPE.set(scope)
        try:return await self.traced.provider.answer(history)
        finally:SCOPE.reset(token);self.lock.release()
    async def close(self):await self.traced.close()
