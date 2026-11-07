"""Explicit opt-in Pi-owned Groq ASR MCP; normal server is unchanged."""
import argparse,logging
from contextlib import asynccontextmanager
import anyio
from mcp.server.fastmcp import FastMCP
from me3_m7.server import ASRService
from me3_core.pool import RequestPool
from speech_mcp.contracts import identity
from me3_assistant.groq_backend import GroqASR,ASR_MODEL

class Service(ASRService):
    max_utterance_seconds=120
    def info(self,*a,**kw):return {**super().info(*a,**kw),'model':ASR_MODEL,'provider':'groq','opt_in':True,'wire_audio_format':'wav','input_language':'en','automatic_language_detection':False}
def make_server(pool=None):
    pool=pool or RequestPool(factory=GroqASR,mode='reuse_after_success');service=Service(pool.lease)
    from me3_assistant.worker_readiness import WorkerReadiness
    readiness=WorkerReadiness(pool,service)
    @asynccontextmanager
    async def lifespan(_):
        try:yield {}
        finally:
            with anyio.move_on_after(2,shield=True) as scope:await pool.shutdown()
            if scope.cancel_called or pool.active is not None or pool.cached is not None:raise RuntimeError('groq_asr_shutdown_failed')
    server=FastMCP('me3-opt-in-groq-asr',lifespan=lifespan,log_level='CRITICAL')
    @server.tool()
    async def server_info()->dict:return {'ok':True,**service.info(),'active_requests':int(pool.active is not None),'poisoned':service.poisoned or pool.poisoned,'network_ready':readiness.ready}
    @server.tool()
    async def network_readiness()->dict:
        """One DNS/TLS preparation of this worker's actual ASR connector; no HTTP/audio/model request."""
        return {**service.info(0,'network-readiness'),**await readiness.check()}
    @server.tool()
    async def request_status(generation:int,request_id:str)->dict:
        identity(generation,request_id);return {'ok':True,**service.info(generation,request_id),**service.states.get(request_id,{'state':'unknown','cleanup_ok':False})}
    @server.tool()
    async def transcribe(pcm_b64:str,generation:int,request_id:str,sample_rate:int=16000,profile:str='low',previous_pcm_b64:str='',previous_generation:int=-1,deadline_ms:int=30000,audio_format:str='wav')->dict:
        return await readiness.transcribe(pcm_b64,generation,request_id,sample_rate,profile,previous_pcm_b64,previous_generation,deadline_ms,audio_format)
    return server
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('role',choices=['asr','tts']);args=p.parse_args();logging.disable(logging.CRITICAL)
    # Same accepted TTS service and resident Piper path. No Groq TTS or key load.
    if args.role=='tts':
        from speech_mcp.server import make_server as piper_server
        server=piper_server('tts')
    else:server=make_server()
    server.run(transport='stdio')
