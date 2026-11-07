"""Real MCP transport with fixture-only ASR HTTP transport; never a provider call."""
import hashlib,json,sys
from types import SimpleNamespace
from me3_assistant.groq_server import make_server
from me3_assistant.groq_backend import GroqASR
from me3_core.pool import RequestPool

TEXT='The full long request is retained. '+('A distinct observation follows. '*38)+'The final request remains present.'
class Wire:
 async def request(self,role,**payload):
  fields={h['name']:value for h,_,value in payload['data']._fields};wav=fields['file']
  assert role=='asr' and fields['language']=='en' and wav[:4]==b'RIFF'
  pcm=wav[44:];assert len(pcm)>15*32000 and pcm[:2]==b'\x01\x00' and pcm[-2:]==b'\x02\x00'
  print(json.dumps(dict(event='fixture_full_pcm_received',bytes=len(pcm),sha256=hashlib.sha256(pcm).hexdigest(),language='en',hosted_calls=0)),file=sys.stderr,flush=True)
  return dict(text=TEXT,language='English')
 async def close(self):pass
class FixtureASR(GroqASR):
 async def prepare_connection(self):
  # Synthetic network preparation for this fixture-only HTTP transport. The
  # actual MCP readiness ownership/epoch guard still runs before transcribe.
  return dict(http_requests=0,model_requests=0,audio_bytes=0,hostname_TLS_verified=True,connector_reused_for_transcription=True,dns_seconds=0.,connection_seconds=0.,dns_cache_seconds=300,keepalive_seconds=60,fixture_only=True)

def factory(emit):return FixtureASR(emit,transport=Wire())
if __name__=='__main__':
 from me3_completion.server import tts_server
 from speech_mcp.tts_config import TTSConfig
 from tests.completion_mcp_fixture import TTS
 server=make_server(RequestPool(factory=factory,mode='reuse_after_success')) if sys.argv[1]=='asr' else tts_server(lambda _:TTS(),TTSConfig(provider='piper',model='synthetic-only',piper_worker_mode='resident'))
 server.run(transport='stdio')
