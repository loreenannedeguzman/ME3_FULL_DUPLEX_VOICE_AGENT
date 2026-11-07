"""Opt-in project-owned ASR MCP: metadata trace and cancellation-safe bounded cleanup."""
import argparse,asyncio,json,logging,os,platform,sys,time
from collections import OrderedDict
import anyio
from mcp.server.fastmcp import FastMCP
from speech_mcp.contracts import identity,decode_pcm,MAX_UTTERANCE_SECONDS
from speech_mcp.provider import ASR_MODEL
from speech_mcp.server import make_server as old_server
from me3_m7.provider import TracedGoogle
BOOT=open('/proc/sys/kernel/random/boot_id').read().strip() if sys.platform=='linux' else None
class ASRService:
    def __init__(self,provider_factory=TracedGoogle,writer=None):self.factory=provider_factory;self.states=OrderedDict();self.lock=asyncio.Lock();self.poisoned=False;self.writer=writer or self.stderr
    def stderr(self,event,**fields):print(json.dumps({'event':'m7_'+event,'observed_ns':time.monotonic_ns(),'clock':'monotonic_ns','boot_id':BOOT,'server_pid':os.getpid(),**fields}),file=sys.stderr,flush=True)
    def info(self,generation=0,request_id='status'):return {'generation':generation,'request_id':request_id,'server_pid':os.getpid(),'server_role':'asr','machine':platform.machine(),'host':platform.system(),'model':os.getenv('ASR_MODEL',ASR_MODEL),'provider':'google'}
    async def call(self,pcm_b64,generation,request_id,sample_rate=16000,profile='low',previous_pcm_b64='',previous_generation=-1,deadline_ms=30000,audio_format='wav'):
        identity(generation,request_id)
        emit=lambda event,**kw:self.writer(event,generation=generation,request_id=request_id,**kw)
        emit('server_received')
        if self.poisoned:raise ValueError('asr_service_cleanup_failed')
        if request_id in self.states:raise ValueError('duplicate_asr_request')
        if audio_format not in {'wav','flac'} or sample_rate!=16000 or profile not in {'low','minimal'} or type(deadline_ms) is not int or not 100<=deadline_ms<=30000:raise ValueError('invalid_asr_configuration')
        maximum=getattr(self,'max_utterance_seconds',MAX_UTTERANCE_SECONDS)
        pcm=decode_pcm(pcm_b64,16000,maximum);previous=decode_pcm(previous_pcm_b64,16000,maximum) if previous_pcm_b64 else None
        if previous and (type(previous_generation) is not int or not 1<=previous_generation<generation or len(previous)+len(pcm)>16000*2*maximum):raise ValueError('invalid_previous_segment')
        self.states[request_id]={'state':'received','generation':generation,'cleanup_ok':False};state=self.states[request_id]
        while len(self.states)>64:
            key=next(iter(self.states))
            if self.states[key]['state'] not in {'released','error'}:raise ValueError('asr_state_capacity')
            self.states.pop(key)
        emit('validation_complete',pcm_bytes=len(pcm),audio_seconds=len(pcm)/32000,sample_rate=16000,channels=1,previous_pcm_bytes=len(previous) if previous else 0)
        provider=None;acquired=False;result=None;cleanup=False
        try:
            emit('queue_wait_start')
            async with asyncio.timeout(3):await self.lock.acquire()
            acquired=True;emit('queue_wait_end');state['state']='active';provider=self.factory(emit)
            seconds=32 if deadline_ms==30000 else deadline_ms/1000
            emit('deadline_armed',operation_seconds=seconds,sdk_timeout_ms=30000)
            async with asyncio.timeout(seconds):value=await provider.transcribe(pcm,profile,previous,audio_format)
            result={'ok':True,**self.info(generation,request_id),**value,'previous_generation':previous_generation if previous else None}
            emit('result_ready',result_chars=len(value.get('text','')),previous_chars=len(value.get('previous_text','')))
        except asyncio.CancelledError:emit('server_cancellation_observed');state['state']='canceling';raise
        except Exception as exc:
            emit('request_failed',error_type=type(exc).__name__);result={'ok':False,**self.info(generation,request_id),'error':'provider_error','error_type':type(exc).__name__,'code':getattr(exc,'code',None)}
        finally:
            state['state']='closing';emit('resource_release_start')
            try:
                with anyio.move_on_after(2,shield=True) as scope:
                    if provider:await provider.close()
                    cleanup=True
                if scope.cancel_called:cleanup=False
            except Exception as exc:emit('resource_release_error',error_type=type(exc).__name__)
            if not cleanup:self.poisoned=True
            if acquired:self.lock.release()
            state.update(state='released' if cleanup else 'error',cleanup_ok=cleanup,released_ns=time.monotonic_ns());emit('resource_released',cleanup_ok=cleanup);emit('worker_completed')
        result['cleanup_ok']=cleanup
        if not cleanup:result.update(ok=False,error='cleanup_failed',error_type='ResourceCleanupError')
        emit('result_returned',ok=result['ok']);return result

def make_server(service=None):
    service=service or ASRService();server=FastMCP('me3-m7-asr',log_level='CRITICAL')
    @server.tool()
    async def server_info()->dict:return {'ok':True,**service.info(),'protocol_transport':'stdio','provider_connected':False,'active_requests':sum(x['state'] not in {'released','error'} for x in service.states.values()),'poisoned':service.poisoned}
    @server.tool()
    async def request_status(generation:int,request_id:str)->dict:
        identity(generation,request_id);state=service.states.get(request_id,{'state':'unknown','generation':generation,'cleanup_ok':False})
        return {'ok':True,**service.info(generation,request_id),**state,'poisoned':service.poisoned}
    @server.tool()
    async def transcribe(pcm_b64:str,generation:int,request_id:str,sample_rate:int=16000,profile:str='low',previous_pcm_b64:str='',previous_generation:int=-1,deadline_ms:int=30000,audio_format:str='wav')->dict:return await service.call(pcm_b64,generation,request_id,sample_rate,profile,previous_pcm_b64,previous_generation,deadline_ms,audio_format)
    return server
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('role',choices=['asr','tts']);a=p.parse_args();logging.disable(logging.CRITICAL)
    (make_server() if a.role=='asr' else old_server('tts')).run(transport='stdio')
