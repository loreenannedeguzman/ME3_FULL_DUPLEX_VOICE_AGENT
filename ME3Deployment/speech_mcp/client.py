"""Real MCP stdio clients; logs metadata only, never tool payloads."""
import asyncio, json, os, sys, uuid
from contextlib import AsyncExitStack
from datetime import timedelta
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.types import ClientNotification,CancelledNotification,CancelledNotificationParams
import anyio
from mcp.client.stdio import stdio_client
from speech_mcp.contracts import identity,decode_pcm,MAX_TTS_SECONDS
from speech_mcp.errors import known_validation_reason
from speech_mcp.tts_config import TTSConfig
ROOT=Path(__file__).resolve().parents[1]

class ProviderLimited(ValueError):
    code=429

class SpeechServiceFailed(ValueError):
    pass

class CancellableSession(ClientSession):
    """Pinned MCP1.26 needs explicit notifications/cancelled on caller cancellation."""
    def __init__(self,*args,events,role,**kwargs):
        super().__init__(*args,**kwargs);self.events=events;self.role=role;self.abandoned=set();self.cancelled_settled=asyncio.Event();self.cancelled_settled.set()
    async def send_request(self,request,*args,**kwargs):
        # Pinned SDK allocates this ID synchronously before its first await.
        request_id=self._request_id
        try:return await super().send_request(request,*args,**kwargs)
        except asyncio.CancelledError:
            if request.root.method=='tools/call':
                self.abandoned.add(request_id);self.cancelled_settled.clear()
                with anyio.CancelScope(shield=True):
                    await self.send_notification(ClientNotification(CancelledNotification(params=CancelledNotificationParams(requestId=request_id,reason='Superseded voice generation'))))
                self.events.emit('mcp_cancel_notification_sent',role=self.role,rpc_request_id=request_id)
            raise
    async def _handle_response(self,message):
        request_id=self._normalize_request_id(message.message.root.id)
        if request_id in self.abandoned:
            self.abandoned.discard(request_id)
            if not self.abandoned:self.cancelled_settled.set()
            self.events.emit('mcp_cancelled_response_discarded',role=self.role,rpc_request_id=request_id)
            return
        await super()._handle_response(message)

class SpeechClients:
    def __init__(self,events,module='speech_mcp.server'):
        self.events=events;self.module=module;self.sessions={};self.stack=AsyncExitStack();self.limit_reached=asyncio.Event();self.failure=asyncio.Event();self.pacer=None;self.speech_languages=('en',);self.tts_identity=None
    async def __aenter__(self):
        if self.module=='speech_mcp.server':
            config=TTSConfig.from_env()
            if config.provider=='google':
                from speech_mcp.rate_limit import TtsRateLimiter
                self.pacer=TtsRateLimiter(ROOT/'.tmp/m3-tts-rate.json',self.events.emit)
        try:
            for role,tool in [('asr','transcribe'),('tts','synthesize')]:
                env={k:v for k,v in os.environ.items() if k in {'GOOGLE_API_KEY','ALLOW_PROVIDER_AUDIO','ASR_MODEL','TTS_MODEL','TEXT_MODEL','M3_VOICE','SYSTEMROOT','PATH','HOME','USERPROFILE','VIRTUAL_ENV','LD_LIBRARY_PATH','TTS_PROVIDER','TTS_TIMEOUT_SECONDS','TTS_FFMPEG_PATH','FLITE_VOICE','PIPER_PYTHON','PIPER_MODEL','PIPER_SPANISH_MODEL','PIPER_WORKER_MODE','ME3_PIPER_CPU_PROFILE','ELEVENLABS_API_KEY','ELEVEN_API_KEY','ELEVENLABS_MODEL','ELEVENLABS_VOICE_ID','ALLOW_ELEVENLABS_TTS','ELEVENLABS_CREDITS_CONFIRMED','ELEVENLABS_BUDGET_ID','ELEVENLABS_REQUEST_BUDGET','ELEVENLABS_CHARACTER_BUDGET'}}
                stderr=self.stack.enter_context((self.events.path.parent/(role+'-server.stderr.log')).open('w',encoding='utf-8'))
                streams=await self.stack.enter_async_context(stdio_client(StdioServerParameters(command=sys.executable,args=['-m',self.module,role],env=env,cwd=str(ROOT)),errlog=stderr))
                session=await self.stack.enter_async_context(CancellableSession(*streams,events=self.events,role=role,read_timeout_seconds=timedelta(seconds=40)))
                initialized=await session.initialize()
                tools=await session.list_tools()
                if tool not in {t.name for t in tools.tools}: raise ValueError('missing_speech_mcp_tool')
                self.sessions[role]=session
                info=await session.call_tool('server_info',{})
                data=self.unpack(info)
                if role=='tts':
                    languages=data.get('speech_languages',['en'])
                    if languages not in (['en'],['en','es']):raise ValueError('invalid_speech_language_registry')
                    self.speech_languages=tuple(languages)
                    self.tts_identity=(data.get('provider'),data.get('model'))
                self.events.emit('mcp_connected',role=role,server_pid=data['server_pid'],host=data['host'],machine=data['machine'],model=data['model'],protocol=initialized.protocolVersion,transport='stdio',provider=data.get('provider'),tts_cpu_profile=data.get('tts_cpu_profile'))
            return self
        except BaseException:
            await self.stack.aclose();raise
    async def __aexit__(self,*args):
        unsettled=False
        try:
            # Pinned SDK sends a cancellation error reply; consume it before closing
            # the session receiver, avoiding a stdio BrokenResourceError shutdown race.
            with anyio.CancelScope(shield=True):
                for role,session in self.sessions.items():
                    try:
                        async with asyncio.timeout(2):await session.cancelled_settled.wait()
                    except TimeoutError:
                        unsettled=True;self.events.emit('mcp_cancellation_settle_timeout',role=role,pending_count=len(session.abandoned))
        finally:result=await self.stack.__aexit__(*args)
        if unsettled and args[0] is None:raise SpeechServiceFailed('mcp_cancel_reply_not_received')
        return result
    @staticmethod
    def unpack(result):
        if result.isError: raise ValueError('mcp_tool_error')
        data=result.structuredContent
        if data is None:
            data=json.loads(next(x.text for x in result.content if x.type=='text'))
        if not isinstance(data,dict):raise ValueError('invalid_mcp_result')
        return data
    def mark_failure(self,stage,code=None):
        if code==429:self.limit_reached.set();return
        if not self.failure.is_set():self.events.emit('speech_service_failed',stage=stage,code=code)
        self.failure.set()
    async def call(self,role,generation,**arguments):
        if self.limit_reached.is_set():
            self.events.emit('mcp_request_blocked_by_quota',role=role,generation=generation)
            raise ProviderLimited('provider_limit_requires_operator_check')
        if self.failure.is_set():
            self.events.emit('mcp_request_blocked_after_error',role=role,generation=generation)
            raise SpeechServiceFailed('speech_service_requires_operator_check')
        if role=='tts' and self.pacer is not None:await self.pacer.acquire()
        request_id=role+'-'+uuid.uuid4().hex
        identity(generation,request_id)
        tool='transcribe' if role=='asr' else 'synthesize'
        self.events.emit('mcp_request_start',role=role,generation=generation,request_id=request_id,tool=tool,argument_bytes=len(arguments.get('pcm_b64','')),text_chars=len(arguments.get('text','')))
        try:
            result=await self.sessions[role].call_tool(tool,{'generation':generation,'request_id':request_id,**arguments},read_timeout_seconds=timedelta(seconds=36))
            data=self.unpack(result)
            if not data.get('ok'):
                self.events.emit('mcp_request_error',role=role,generation=generation,request_id=request_id,error=data.get('error'),error_type=data.get('error_type'),code=data.get('code'),validation_reason=known_validation_reason(data.get('validation_reason')))
                if data.get('code')==429:
                    self.limit_reached.set()
                    raise ProviderLimited('provider_rate_or_quota_limit')
                self.mark_failure(role,data.get('code'))
                raise ValueError('speech_mcp_request_failed')
            if data.get('generation')!=generation or data.get('request_id')!=request_id or data.get('server_role')!=role:
                raise ValueError('mcp_result_identity_mismatch')
            if role=='tts':
                if 'language' in arguments or self.speech_languages==('en','es'):
                    language=arguments.get('language','en')
                    if language not in self.speech_languages or data.get('language')!=language:raise ValueError('tts_result_language_mismatch')
                    if language=='es' and (data.get('provider')!='piper' or data.get('model')!='piper:es_ES-sharvard-medium' or type(data.get('speaker_id')) is not int or data['speaker_id']!=1):raise ValueError('tts_result_voice_mismatch')
                    if language=='en' and self.speech_languages==('en','es') and (self.tts_identity is None or (data.get('provider'),data.get('model'))!=self.tts_identity):raise ValueError('tts_result_voice_mismatch')
                if data.get('sample_rate')!=24000 or data.get('channels')!=1 or data.get('encoding')!='pcm_s16le':raise ValueError('invalid_tts_result_format')
                pcm=decode_pcm(data.get('pcm_b64'),24000,MAX_TTS_SECONDS)
                if data.get('byte_count')!=len(pcm):raise ValueError('tts_result_size_mismatch')
            if role=='tts' and isinstance(data.get('tts_metrics'),dict):
                allowed={'backend_first_pcm_seconds','backend_complete_seconds','native_sample_rate','worker_pid','worker_epoch','cold_start','reuse_index','model_import_seconds','model_load_seconds'}
                metrics={k:v for k,v in data['tts_metrics'].items() if k in allowed and type(v) in (int,float,bool)}
                self.events.emit('tts_backend_metrics',generation=generation,request_id=request_id,**metrics)
            self.events.emit('mcp_request_end',role=role,generation=generation,request_id=request_id,server_pid=data['server_pid'],result_bytes=data.get('byte_count',0),result_chars=len(data.get('text','')),source_mime=data.get('source_mime'),provider_api=data.get('provider_api'),speech_language=data.get('language') if role=='tts' else None,model=data.get('model'))
            return data
        except asyncio.CancelledError:
            self.events.emit('mcp_request_cancelled',role=role,generation=generation,request_id=request_id)
            raise
        except Exception as exc:
            self.mark_failure(role,getattr(exc,'code',None));raise
