import argparse,asyncio,logging
from me3_core.pool import RequestPool
from me3_core.server import make_server
from speech_mcp.server import make_server as tts
class Fake:
    def __init__(self,emit):self.emit=emit
    async def transcribe(self,pcm,*args):
        self.emit('fixture_request',synthetic=True)
        if pcm[0]==1:await asyncio.Event().wait()
        return {'text':'synthetic replacement'}
    async def close(self):await asyncio.sleep(.02)
p=argparse.ArgumentParser();p.add_argument('role');a=p.parse_args();logging.disable(logging.CRITICAL)
(make_server(RequestPool(Fake)) if a.role=='asr' else tts('tts')).run(transport='stdio')
