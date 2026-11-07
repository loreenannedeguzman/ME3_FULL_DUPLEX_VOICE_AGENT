"""Actual MCP transports, entirely synthetic inference; no provider traffic."""
import argparse,asyncio,json,logging,sys
from me3_core.pool import RequestPool
from me3_core.server import make_server
from me3_completion.server import tts_server
from speech_mcp.tts_config import TTSConfig
class ASR:
    def __init__(self,emit):self.emit=emit
    async def transcribe(self,pcm,*args):return {'text':'synthetic only'}
    async def close(self):pass
class TTS:
    source_mime='audio/L16;rate=24000';provider_api='synthetic';metrics={}
    async def synthesize_identified(self,text,style,generation,request_id):
        if text=='sleep':await asyncio.Event().wait()
        return bytes(9600)
    async def close(self):print(json.dumps({'event':'fixture_tts_closed'}),file=sys.stderr,flush=True)
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('role');a=p.parse_args();logging.disable(logging.CRITICAL)
    (make_server(RequestPool(ASR)) if a.role=='asr' else tts_server(lambda _:TTS(),TTSConfig(provider='piper',model='synthetic-only',piper_worker_mode='resident'))).run(transport='stdio')
