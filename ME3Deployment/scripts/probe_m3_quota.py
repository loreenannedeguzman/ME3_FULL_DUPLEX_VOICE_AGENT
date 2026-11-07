"""Real local pipeline/MCP with an injected429; no provider, no live speech proof."""
import asyncio,json,logging,sys,time
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from loguru import logger
logger.remove();logging.disable(logging.CRITICAL)
import mcp_voice_agent as app
from speech_mcp.client import SpeechClients
from voice_agent import Settings,EventLog,configure_pi_audio
class NoProvider:
    async def close(self):pass
    async def answer(self,*args):raise AssertionError('Provider must not be called')
app.GoogleSpeech=NoProvider
app.SpeechClients=lambda events:SpeechClients(events,module='tests.mcp_quota_fixture_server')
settings=Settings(api_key='',max_seconds=10);configure_pi_audio(settings)
events=EventLog(ROOT/'evidence/m3'/('quota-fixture-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+str(time.monotonic_ns())))
started=time.monotonic()
try:
    try:asyncio.run(app.run(settings,events))
    except Exception:pass # Error is expected; acceptance below checks the actual stop path.
    passed=(events.counts.get('session_provider_limited')==1 and events.counts.get('session_end')==1 and events.counts.get('duplex_transport_closed')==1 and events.counts.get('mcp_request_start')==1 and time.monotonic()-started<10)
    events.emit('synthetic_quota_stop_result',passed=passed,provider_called=False,live_conversation='NOT_TESTED')
    print(json.dumps({'scripted_quota_stop':'PASS' if passed else 'FAIL','provider_called':False,'live_conversation':'NOT_TESTED','elapsed_seconds':time.monotonic()-started}))
finally:events.close()
raise SystemExit(0 if passed else 2)
