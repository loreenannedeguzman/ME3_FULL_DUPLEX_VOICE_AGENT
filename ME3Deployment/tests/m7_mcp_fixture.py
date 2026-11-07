import argparse,asyncio,logging
from me3_m7.server import ASRService,make_server
from speech_mcp.server import make_server as old
class FixtureProvider:
    def __init__(self,emit):self.emit=emit
    async def transcribe(self,pcm,profile,previous,audio_format='wav'):
        self.emit('provider_request_start',synthetic=True)
        if pcm[0]==1:await asyncio.Event().wait()
        return {'text':'synthetic replacement'}
    async def close(self):await asyncio.sleep(.05)
p=argparse.ArgumentParser();p.add_argument('role');a=p.parse_args();logging.disable(logging.CRITICAL)
(make_server(ASRService(FixtureProvider)) if a.role=='asr' else old('tts')).run(transport='stdio')
