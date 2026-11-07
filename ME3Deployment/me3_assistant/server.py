"""Normal-mode student ASR MCP uses bounded transport; TTS delegation unchanged."""
import argparse,json,logging
from pathlib import Path
from me3_core.pool import RequestPool
from me3_core.server import make_server as core_asr_server
from speech_mcp.server import make_server as tts_server
from me3_assistant.transport import BoundedScopedGoogle

def make_asr_server(pool=None):
    config=json.loads((Path(__file__).resolve().parents[1]/'config/core_agent.json').read_text(encoding='utf-8'))
    return core_asr_server(pool=pool or RequestPool(factory=BoundedScopedGoogle,mode=config['asr_connection_mode']))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('role',choices=['asr','tts']);args=p.parse_args();logging.disable(logging.CRITICAL)
    (make_asr_server() if args.role=='asr' else tts_server('tts')).run(transport='stdio')
