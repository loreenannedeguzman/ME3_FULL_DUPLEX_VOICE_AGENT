"""Opt-in ASR MCP client: release barriers, strict identity, bounded explicit timeout recovery."""
import asyncio,json,time,uuid
from datetime import timedelta
from pathlib import Path
from speech_mcp.client import SpeechClients,ProviderLimited,SpeechServiceFailed
from speech_mcp.contracts import identity,decode_pcm,MAX_UTTERANCE_SECONDS
class RecoverableASRTimeout(TimeoutError):pass
class RecoverableASRTransportError(ConnectionError):pass
CLEAN_ASR_DISCONNECT_ERRORS=frozenset({'ClientOSError','ServerDisconnectedError'})
class M7SpeechClients(SpeechClients):
    def __init__(self,events,module='me3_m7.server',profile=None):
        super().__init__(events,module);config=Path(__file__).resolve().parents[1]/'config/m7_asr.json'
        self.profile=profile or (json.loads(config.read_text())['profile'] if config.exists() else 'low')
        if self.profile not in {'low','minimal'}:raise ValueError('explicit_asr_profile_required')
        self.audio_format=json.loads(config.read_text()).get('audio_format','wav') if config.exists() else 'wav'
        if self.audio_format not in {'wav','flac'}:raise ValueError('explicit_audio_format_required')
        self.pending={};self.recovered_timeouts=0;self.recovered_transports=0
    async def __aenter__(self):
        result=await super().__aenter__()
        if self.module=='me3_m7.server':
            from speech_mcp.tts_config import TTSConfig
            if TTSConfig.from_env().provider=='google':
                from speech_mcp.rate_limit import TtsRateLimiter
                self.pacer=TtsRateLimiter(Path(__file__).resolve().parents[1]/'.tmp/m3-tts-rate.json',self.events.emit)
        return result
    async def released(self):
        for rid,g in list(self.pending.items()):
            self.events.emit('m7_release_barrier_start',generation=g,request_id=rid)
            try:
                async with asyncio.timeout(3):
                    while True:
                        result=self.unpack(await self.sessions['asr'].call_tool('request_status',{'generation':g,'request_id':rid},read_timeout_seconds=timedelta(seconds=1)))
                        if result.get('request_id')!=rid or result.get('generation')!=g or result.get('server_role')!='asr' or result.get('poisoned'):raise ValueError('asr_release_identity_or_state')
                        if result.get('state')=='released' and result.get('cleanup_ok') is True:break
                        await asyncio.sleep(.05)
                self.events.emit('m7_release_barrier_complete',generation=g,request_id=rid,server_released_ns=result.get('released_ns'));self.pending.pop(rid)
            except asyncio.CancelledError:raise
            except Exception:
                self.mark_failure('asr_cleanup');raise
    async def call(self,role,generation,**arguments):
        if role!='asr':return await super().call(role,generation,**arguments)
        if self.limit_reached.is_set():raise ProviderLimited('provider_limit_requires_operator_check')
        if self.failure.is_set():raise SpeechServiceFailed('speech_service_requires_operator_check')
        await self.released();rid='asr-'+uuid.uuid4().hex;identity(generation,rid)
        pcm=decode_pcm(arguments.get('pcm_b64'),16000,getattr(self,'max_utterance_seconds',MAX_UTTERANCE_SECONDS))
        self.events.emit('m7_asr_payload',generation=generation,request_id=rid,pcm_bytes=len(pcm),audio_seconds=len(pcm)/32000,sample_rate=16000,channels=1,previous_present=bool(arguments.get('previous_pcm_b64')))
        self.events.emit('mcp_request_start',role='asr',generation=generation,request_id=rid,tool='transcribe',argument_bytes=len(arguments.get('pcm_b64','')),text_chars=0)
        self.events.emit('m7_client_dispatch',generation=generation,request_id=rid,observed_ns=time.monotonic_ns(),profile=self.profile)
        try:
            result=await self.sessions['asr'].call_tool('transcribe',{'generation':generation,'request_id':rid,'profile':self.profile,'audio_format':self.audio_format,**arguments},read_timeout_seconds=timedelta(seconds=36));data=self.unpack(result)
            self.events.emit('m7_client_result_received',generation=generation,request_id=rid)
            if data.get('generation')!=generation or data.get('request_id')!=rid or data.get('server_role')!='asr':raise ValueError('mcp_result_identity_mismatch')
            if data.get('cleanup_ok') is not True:raise ValueError('asr_resources_not_released')
            if not data.get('ok'):
                self.events.emit('mcp_request_error',role='asr',generation=generation,request_id=rid,error=data.get('error'),error_type=data.get('error_type'),code=data.get('code'))
                if data.get('code')==429:self.limit_reached.set();raise ProviderLimited('provider_rate_or_quota_limit')
                if (data.get('error')=='provider_error' and data.get('code') is None
                    and data.get('error_type') in CLEAN_ASR_DISCONNECT_ERRORS
                    and data.get('transport_recovery_ready') is True and self.recovered_transports<2):
                    self.recovered_transports+=1
                    self.events.emit('m7_clean_transport_recoverable',generation=generation,request_id=rid,count=self.recovered_transports,error_type=data['error_type'],transport_recovery_ready=True,automatic_retry=False)
                    raise RecoverableASRTransportError('cleaned_asr_transport_disconnect')
                if (data.get('error_type') in {'TimeoutError','ReadTimeout'}
                    or data.get('error_type')=='ConnectionTimeoutError' and data.get('error')=='provider_error'
                    and data.get('code') is None and data.get('timeout_recovery_ready') is True) and self.recovered_timeouts<2:
                    self.recovered_timeouts+=1;self.events.emit('m7_clean_timeout_recoverable',generation=generation,request_id=rid,count=self.recovered_timeouts,automatic_retry=False);raise RecoverableASRTimeout('cleaned_asr_timeout')
                raise SpeechServiceFailed('asr_failure_requires_operator_check')
            if not isinstance(data.get('text'),str) or len(data['text'])>getattr(self,'max_transcript_chars',1000):raise ValueError('invalid_asr_text')
            if arguments.get('previous_pcm_b64') and (data.get('previous_generation')!=arguments.get('previous_generation') or not isinstance(data.get('previous_text'),str) or len(data['previous_text'])>getattr(self,'max_transcript_chars',1000)):raise ValueError('invalid_previous_segment_result')
            self.events.emit('mcp_request_end',role='asr',generation=generation,request_id=rid,server_pid=data['server_pid'],result_bytes=0,result_chars=len(data['text']),source_mime=None,provider_api=None);return data
        except (RecoverableASRTimeout,RecoverableASRTransportError):raise
        except asyncio.CancelledError:
            self.pending[rid]=generation;self.events.emit('mcp_request_cancelled',role='asr',generation=generation,request_id=rid);raise
        except Exception as exc:self.mark_failure('asr',getattr(exc,'code',None));raise
    async def __aexit__(self,*args):
        # On ordinary shutdown, also require the release marker, not just SDK cancellation acknowledgement.
        error=None
        try:
            if self.pending:
                import anyio
                with anyio.CancelScope(shield=True):await self.released()
        except BaseException as exc:error=exc
        result=await super().__aexit__(*args)
        if error is not None:raise error
        return result
