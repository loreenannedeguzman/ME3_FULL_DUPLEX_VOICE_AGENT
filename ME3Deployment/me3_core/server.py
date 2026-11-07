"""Project-owned core ASR MCP; unchanged M7 public tools and TTS delegation."""
import argparse,logging,json
from pathlib import Path
from contextlib import asynccontextmanager
import anyio
from mcp.server.fastmcp import FastMCP
from me3_m7.server import ASRService
from speech_mcp.server import make_server as tts_server
from speech_mcp.contracts import identity
from me3_core.pool import RequestPool
ROOT=Path(__file__).resolve().parents[1]

def make_server(pool=None):
    config=json.loads((ROOT/'config/core_agent.json').read_text(encoding='utf-8'));pool=pool or RequestPool(mode=config['asr_connection_mode']);service=ASRService(pool.lease)
    @asynccontextmanager
    async def lifespan(_):
        try:yield {'asr_pool':pool}
        finally:
            with anyio.move_on_after(2,shield=True) as scope:await pool.shutdown()
            ok=not scope.cancel_called and pool.active is None and pool.cached is None
            service.stderr('core_pool_shutdown',generation=0,request_id='lifecycle',cleanup_ok=ok)
            if not ok:raise RuntimeError('asr_pool_shutdown_failed')
    server=FastMCP('me3-core-asr',lifespan=lifespan,log_level='CRITICAL')
    @server.tool()
    async def server_info()->dict:return {'ok':True,**service.info(),'protocol_transport':'stdio','provider_connected':pool.cached is not None,'active_requests':int(pool.active is not None),'poisoned':service.poisoned or pool.poisoned,'connection_mode':pool.mode}
    @server.tool()
    async def request_status(generation:int,request_id:str)->dict:
        identity(generation,request_id);state=service.states.get(request_id,{'state':'unknown','generation':generation,'cleanup_ok':False})
        return {'ok':True,**service.info(generation,request_id),**state,'poisoned':service.poisoned or pool.poisoned}
    @server.tool()
    async def transcribe(pcm_b64:str,generation:int,request_id:str,sample_rate:int=16000,profile:str='low',previous_pcm_b64:str='',previous_generation:int=-1,deadline_ms:int=30000,audio_format:str='wav')->dict:return await service.call(pcm_b64,generation,request_id,sample_rate,profile,previous_pcm_b64,previous_generation,deadline_ms,audio_format)
    return server
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('role',choices=['asr','tts']);args=p.parse_args();logging.disable(logging.CRITICAL)
    (make_server() if args.role=='asr' else tts_server('tts')).run(transport='stdio')
