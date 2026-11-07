"""One local neutral acknowledgment cache inside the student-owned TTS MCP."""
import asyncio
from speech_mcp.tts_adapters import validate_pcm
CUE='Uh-huh.'
class CueCache:
    def __init__(self,delegate):self.delegate=delegate;self.audio=None;self.metrics={};self.lock=asyncio.Lock()
    def __getattr__(self,name):return getattr(self.delegate,name)
    async def synthesize_identified(self,text,style,generation,request_id):
        async with self.lock:
            if text==CUE and style=='normal' and self.audio is not None:
                await asyncio.sleep(0)
                self.metrics={'completion_cue_cache_hit':True,'cached_pcm_seconds':len(self.audio)/48000}
                return self.audio
            pcm=validate_pcm(await self.delegate.synthesize_identified(text,style,generation,request_id))
            self.metrics={**getattr(self.delegate,'metrics',{}),'completion_cue_cache_hit':False}
            if text==CUE and style=='normal':
                if len(pcm)>=48000:raise ValueError('backchannel_audio_not_under_one_second')
                self.audio=pcm
            return pcm
    async def close(self):self.audio=None;await self.delegate.close()
