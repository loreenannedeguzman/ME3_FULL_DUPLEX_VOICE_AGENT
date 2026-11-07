"""Explicit opt-in servers; unchanged ASR lifecycle and stable bounded TTS contract."""
import argparse,asyncio,base64,logging,os,platform,json
from pathlib import Path
from contextlib import asynccontextmanager
import anyio
from mcp.server.fastmcp import FastMCP
from me3_core.server import make_server as asr_server
from me3_completion.cache import CueCache
from me3_completion.asr import StructuredGoogle
from me3_core.pool import RequestPool
from speech_mcp.tts_config import TTSConfig
from speech_mcp.tts_adapters import make_tts_adapter
from speech_mcp.contracts import identity,validate_text
from speech_mcp.errors import validation_reason

def completion_asr_server():
    config=json.loads((Path(__file__).resolve().parents[1]/'config/core_agent.json').read_text(encoding='utf-8'))
    return asr_server(pool=RequestPool(factory=StructuredGoogle,mode=config['asr_connection_mode']))

def tts_server(factory=make_tts_adapter,config=None):
    config=config or TTSConfig.from_env();resident=config.provider=='piper' and config.piper_worker_mode=='resident';shared=None
    @asynccontextmanager
    async def lifespan(_):
        nonlocal shared
        try:yield {}
        finally:
            if shared is not None:
                with anyio.CancelScope(shield=True):await shared.close()
    server=FastMCP('me3-completion-tts',lifespan=lifespan,log_level='CRITICAL')
    def info(generation,request_id):return {'generation':generation,'request_id':request_id,'server_pid':os.getpid(),'server_role':'tts','machine':platform.machine(),'host':platform.system(),'model':config.model,'provider':config.provider}
    @server.tool()
    async def server_info()->dict:return {'ok':True,**info(0,'status'),'protocol_transport':'stdio','provider_connected':False}
    @server.tool()
    async def synthesize(text:str,generation:int,request_id:str,style:str='normal')->dict:
        nonlocal shared
        try:
            identity(generation,request_id);clean=validate_text(text)
            if style not in {'normal','slow'}:raise ValueError('invalid_style')
        except ValueError as exc:return {'ok':False,'error':str(exc)}
        adapter=None
        try:
            if resident:
                if shared is None:shared=CueCache(factory(config))
                adapter=shared
            else:adapter=factory(config)
            async with asyncio.timeout(32):pcm=await adapter.synthesize_identified(clean,style,generation,request_id) if resident else await adapter.synthesize(clean,style)
            return {'ok':True,**info(generation,request_id),'pcm_b64':base64.b64encode(pcm).decode(),'sample_rate':24000,'channels':1,'encoding':'pcm_s16le','byte_count':len(pcm),'source_mime':adapter.source_mime,'provider_api':adapter.provider_api,'tts_metrics':getattr(adapter,'metrics',{})}
        except asyncio.CancelledError:raise
        except Exception as exc:return {'ok':False,**info(generation,request_id),'error':'provider_error','error_type':type(exc).__name__,'code':getattr(exc,'code',None),'validation_reason':validation_reason(exc)}
        finally:
            if adapter and not resident:await adapter.close()
    return server
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('role',choices=['asr','tts']);args=p.parse_args();logging.disable(logging.CRITICAL)
    (completion_asr_server() if args.role=='asr' else tts_server()).run(transport='stdio')
