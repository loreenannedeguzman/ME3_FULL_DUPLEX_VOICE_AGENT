"""Pi-owned stable speech MCP tools; TTS backend selected only by local config."""
import argparse,asyncio,base64,logging,os,platform
from contextlib import asynccontextmanager
import anyio
from pathlib import Path
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from speech_mcp.contracts import identity,decode_pcm,validate_text,MAX_UTTERANCE_SECONDS
from speech_mcp.provider import GoogleSpeech,ASR_MODEL
from speech_mcp.errors import validation_reason
from speech_mcp.tts_config import TTSConfig
from speech_mcp.tts_adapters import make_tts_adapter

ROOT=Path(__file__).resolve().parents[1]

def make_server(role):
    if role not in {'asr','tts'}:raise ValueError('invalid_speech_role')
    config=TTSConfig.from_env() if role=='tts' else None
    resident=config is not None and config.provider=='piper' and config.piper_worker_mode=='resident'
    shared={};tts_lock=asyncio.Lock()
    @asynccontextmanager
    async def lifespan(_):
        nonlocal shared
        try:yield {}
        finally:
            with anyio.CancelScope(shield=True):
                failures=[]
                for provider in shared.values():
                    try:await provider.close()
                    except Exception as exc:failures.append(exc)
                if failures:raise failures[0]
    server=FastMCP('me3-'+role,log_level='CRITICAL',lifespan=lifespan)
    def info(generation,request_id,selected=None):
        selected=selected or config
        return {'generation':generation,'request_id':request_id,'server_pid':os.getpid(),'server_role':role,'machine':platform.machine(),'host':platform.system(),'model':os.getenv('ASR_MODEL',ASR_MODEL) if role=='asr' else selected.model,'provider':'google' if role=='asr' else selected.provider,**(dict(speaker_id=selected.piper_speaker_id) if role=='tts' and selected.piper_speaker_id is not None else {})}
    async def perform(generation,request_id,operation,selected=None):
        selected=selected or config
        nonlocal shared
        provider=None
        try:
            if resident:
                if selected.voice not in shared:shared[selected.voice]=make_tts_adapter(selected)
                provider=shared[selected.voice]
            else:provider=GoogleSpeech() if role=='asr' else make_tts_adapter(selected)
            async with asyncio.timeout(32):value=await operation(provider)
            return {'ok':True,**info(generation,request_id,selected),**value}
        except asyncio.CancelledError:raise
        except Exception as exc:
            return {'ok':False,**info(generation,request_id,selected),'error':'provider_error','error_type':type(exc).__name__,'code':getattr(exc,'code',None),'validation_reason':validation_reason(exc)}
        finally:
            if provider and not resident:await provider.close()
    @server.tool()
    async def server_info()->dict:
        """Nonsecret provenance only; no provider request or credit/access claim."""
        if config is not None:config.require_languages_ready()
        return {'ok':True,**({'speech_languages':list(config.speech_languages())} if config is not None else {}),**info(0,'status'),'protocol_transport':'stdio','provider_connected':False,**({'tts_cpu_profile':os.getenv('ME3_PIPER_CPU_PROFILE')} if role=='tts' else {})}
    if role=='asr':
        @server.tool()
        async def transcribe(pcm_b64:str,generation:int,request_id:str,sample_rate:int=16000)->dict:
            """Recognize bounded mono PCM16; no file paths."""
            try:
                identity(generation,request_id)
                if sample_rate!=16000:raise ValueError('sample_rate_must_be_16000')
                pcm=decode_pcm(pcm_b64,sample_rate,MAX_UTTERANCE_SECONDS)
            except ValueError as exc:return {'ok':False,'error':str(exc)}
            async def operation(p):return {'text':await p.transcribe(pcm)}
            return await perform(generation,request_id,operation)
    else:
        @server.tool()
        async def synthesize(text:str,generation:int,request_id:str,style:str='normal',language:str='en')->dict:
            """Bounded text ->24kHz mono PCM16; explicit configured backend only."""
            try:
                identity(generation,request_id);clean=validate_text(text)
                if style not in {'normal','slow'}:raise ValueError('invalid_style')
                selected=config.for_language(language)
            except ValueError as exc:return {'ok':False,'error':str(exc)}
            async def operation(p):
                pcm=await p.synthesize_identified(clean,style,generation,request_id) if resident else await p.synthesize(clean,style)
                return {'language':language,'pcm_b64':base64.b64encode(pcm).decode(),'sample_rate':24000,'channels':1,'encoding':'pcm_s16le','byte_count':len(pcm),'source_mime':p.source_mime,'provider_api':p.provider_api,'tts_metrics':getattr(p,'metrics',{})}
            if resident:
                async with tts_lock:return await perform(generation,request_id,operation,selected)
            return await perform(generation,request_id,operation,selected)
    return server

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('role',choices=['asr','tts']);args=parser.parse_args()
    load_dotenv(ROOT/'.env',override=False,encoding='utf-8-sig')
    logging.disable(logging.CRITICAL);make_server(args.role).run(transport='stdio')
if __name__=='__main__':main()
