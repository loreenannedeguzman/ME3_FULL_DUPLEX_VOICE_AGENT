"""One actual-worker DNS/TLS preparation; no HTTP, audio, admission or model call."""
import asyncio,time
from datetime import timedelta
import anyio
from yarl import URL
from me3_core.pool import SCOPE,RequestScope,dispatch
from speech_mcp.client import SpeechClients,SpeechServiceFailed
from me3_m7.client import CLEAN_ASR_DISCONNECT_ERRORS

PREPARE_SECONDS=25
DNS_SECONDS=20

async def resolve_for_request(transport):
    """Refresh the actual connector cache before the single ASR wire attempt.

    Uses the same bounded system resolver/cache as startup readiness. No HTTP,
    audio/model request, IP pin or automatic retry. The caller's existing30s
    total deadline covers this lookup and the later10s connection/HTTP work.
    """
    public_dns=getattr(transport,'prepare_dns',None)
    if public_dns is not None:return await public_dns()
    from me3_assistant.groq_backend import ENDPOINTS
    if not transport.owned or not transport.active:raise ValueError('owned_active_asr_transport_required')
    session=transport.ensure_session('asr');endpoint=URL(ENDPOINTS['asr'])
    if endpoint.scheme!='https' or endpoint.host is None or endpoint.port!=443:
        raise ValueError('asr_readiness_requires_provider_https')
    started=time.monotonic();connector=session.connector;key=(endpoint.host,443)
    cached=key in connector._cached_hosts and not connector._cached_hosts.expired(key)
    transport.emit('groq_asr_prewire_dns_start',wire_attempts=0,automatic_retry=False)
    try:
        if not cached:
            async with asyncio.timeout(DNS_SECONDS):
                records=await connector._resolve_host(endpoint.host,443)
            if not records:raise ValueError('empty_asr_resolution')
            # _resolve_host advances the cache's round-robin cursor. Restore
            # this fresh result's initial order so the later single POST still
            # tries every address in the same order as a cold HTTP connection.
            # Never re-add a cache hit: its original expiry must not slide.
            connector._cached_hosts.add(key,records)
        transport.emit('groq_asr_prewire_dns_ready',seconds=round(time.monotonic()-started,4),cache_hit=cached,wire_attempts=0,automatic_retry=False)
    except BaseException as exc:
        transport.emit('groq_asr_prewire_dns_failed',stage='dns',error_type=type(exc).__name__,wire_attempts=0,automatic_retry=False)
        raise

async def prepare_connection(transport):
    from aiohttp import ClientRequest
    from aiohttp.helpers import get_env_proxy_for_url
    from me3_assistant.groq_backend import ENDPOINTS
    if not transport.owned or transport.active:raise ValueError('owned_idle_asr_transport_required')
    session=transport.ensure_session('asr');connector=session.connector
    endpoint=URL(ENDPOINTS['asr'])
    if endpoint.scheme!='https' or endpoint.host is None or endpoint.port!=443:
        raise ValueError('asr_readiness_requires_provider_https')
    started=time.monotonic();transport.readiness_stage='dns';connection=None
    try:
        async with asyncio.timeout(PREPARE_SECONDS):
            async with asyncio.timeout(DNS_SECONDS):
                records=await connector._resolve_host(endpoint.host,443)
            if not records:raise ValueError('empty_asr_resolution')
            dns=time.monotonic()-started;transport.readiness_stage='tcp_tls'
            proxy=proxy_auth=None
            if session.trust_env:
                try:proxy,proxy_auth=get_env_proxy_for_url(endpoint)
                except LookupError:pass
            request=ClientRequest('POST',endpoint,loop=asyncio.get_running_loop(),proxy=proxy,proxy_auth=proxy_auth,ssl=True,session=session)
            connection=await connector.connect(request,[],session.timeout)
            tls=connection.transport.get_extra_info('ssl_object')
            if tls is None or not tls.getpeercert():raise ValueError('asr_readiness_verified_TLS_required')
            # Release this exact verified socket into the same ClientSession
            # pool later used by transcribe. Never call request.send/session.post.
            connection.release();connection=None
            transport.readiness_stage='ready'
            return dict(dns_seconds=round(dns,4),connection_seconds=round(time.monotonic()-started-dns,4),total_seconds=round(time.monotonic()-started,4),hostname_TLS_verified=True,connector_reused_for_transcription=True,http_requests=0,model_requests=0,audio_bytes=0,dns_cache_seconds=connector._cached_hosts._ttl,keepalive_seconds=connector._keepalive_timeout)
    finally:
        if connection is not None:connection.close()

class WorkerReadiness:
    def __init__(self,pool,service):
        self.pool=pool;self.service=service;self.attempted=False;self.ready=False;self.provider=None;self.epoch=None;self.failed_timeout_generation=None;self.failed_transport_generation=None
    def usable(self):
        return (self.ready and not self.pool.closed and not self.pool.poisoned and not self.service.poisoned
                and self.pool.cached is self.provider and self.pool.epoch==self.epoch)
    async def transcribe(self,*args,**kwargs):
        # Readiness belongs to this worker, while each canceled user generation
        # retires its own connection. A NEW user request may borrow a new
        # transport after proved release; never replay the canceled request.
        if not self.usable():raise ValueError('asr_worker_not_ready')
        request_id=kwargs.get('request_id',args[2] if len(args)>2 else None)
        generation=kwargs.get('generation',args[1] if len(args)>1 else None)
        if self.failed_timeout_generation is not None and (type(generation) is not int or generation<=self.failed_timeout_generation):
            raise ValueError('new_user_generation_required_after_asr_timeout')
        if self.failed_transport_generation is not None and (type(generation) is not int or generation<=self.failed_transport_generation):
            raise ValueError('new_user_generation_required_after_asr_disconnect')
        succeeded=False;canceled=False;clean_timeout=False;clean_transport=False
        try:
            result=await self.service.call(*args,**kwargs)
            result.pop('timeout_recovery_ready',None)
            result.pop('transport_recovery_ready',None) # witness is host-owned, never provider supplied
            succeeded=result.get('ok') is True
            clean_timeout=(result.get('ok') is False and result.get('cleanup_ok') is True
                           and result.get('error_type') in {'TimeoutError','ReadTimeout','ConnectionTimeoutError'}
                           and result.get('code') is None and result.get('generation')==generation
                           and result.get('request_id')==request_id and result.get('server_role')=='asr')
            clean_transport=(result.get('ok') is False and result.get('cleanup_ok') is True
                             and result.get('error')=='provider_error' and result.get('code') is None
                             and result.get('error_type') in CLEAN_ASR_DISCONNECT_ERRORS
                             and result.get('generation')==generation and result.get('request_id')==request_id
                             and result.get('server_role')=='asr')
            return result
        except asyncio.CancelledError:
            canceled=True;raise
        finally:
            released=self.service.states.get(request_id,{})
            clean=(released.get('state')=='released' and released.get('cleanup_ok') is True
                   and self.pool.active is None and not self.pool.closed
                   and not self.pool.poisoned and not self.service.poisoned)
            expected_epoch=self.pool.epoch in (self.epoch,self.epoch+1)
            expected_owner=(self.pool.cached is self.provider or self.provider is None
                            and self.pool.epoch==self.epoch+1 or (canceled or clean_timeout or clean_transport) and self.pool.cached is None)
            if ((succeeded or canceled or clean_timeout or clean_transport) and clean and expected_epoch and expected_owner and (not clean_transport or self.pool.cached is None and released.get('generation')==generation)
                and (not clean_timeout or result.get('error_type')!='ConnectionTimeoutError'
                     or result.get('error')=='provider_error' and self.pool.cached is None
                     and released.get('generation')==generation)):
                self.provider=self.pool.cached;self.epoch=self.pool.epoch
                if clean_timeout:
                    self.failed_timeout_generation=generation
                    if result.get('error_type')=='ConnectionTimeoutError':result['timeout_recovery_ready']=True
                if clean_transport:
                    self.failed_transport_generation=generation
                    result['transport_recovery_ready']=True
                self.service.writer('asr_worker_request_released',connection_epoch=self.epoch,
                                    canceled=canceled,clean_timeout=clean_timeout,clean_transport=clean_transport,connection_retired=self.provider is None,
                                    next_distinct_user_request_allowed=True,automatic_retry=False)
            else:self.ready=False

    async def check(self):
        if self.attempted:return dict(ok=False,error='readiness_already_attempted',http_requests=0,model_requests=0,audio_bytes=0)
        self.attempted=True;provider=None;acquired=False;token=None
        try:
            async with asyncio.timeout(3):await self.service.lock.acquire()
            acquired=True
            if self.pool.closed or self.pool.poisoned or self.service.poisoned or self.pool.active is not None or self.pool.cached is not None or self.service.states:
                raise ValueError('asr_readiness_requires_unused_worker')
            token=SCOPE.set(RequestScope(self.service.writer))
            provider=self.pool.factory(dispatch)
            result=await provider.prepare_connection()
            self.pool.cached=provider;self.pool.epoch+=1;self.pool.uses=0
            self.provider=provider;self.epoch=self.pool.epoch;self.ready=True
            self.service.writer('asr_worker_network_ready',connection_epoch=self.epoch,HTTP_requests=0,model_requests=0)
            return dict(ok=True,network_ready=True,connection_epoch=self.epoch,automatic_retry=False,**result)
        except asyncio.CancelledError:raise
        except Exception as exc:
            stage=getattr(getattr(provider,'transport',None),'readiness_stage','initialization')
            if stage not in {'dns','tcp_tls','ready'}:stage='initialization'
            self.service.writer('asr_worker_network_readiness_failed',stage=stage,error_type=type(exc).__name__,automatic_retry=False)
            return dict(ok=False,network_ready=False,error='worker_network_not_ready',stage=stage,error_type=type(exc).__name__,http_requests=0,model_requests=0,audio_bytes=0,automatic_retry=False)
        finally:
            try:
                if provider is not None and not self.ready:
                    with anyio.move_on_after(2,shield=True) as scope:await provider.close()
                    if scope.cancel_called:self.pool.poisoned=True;self.service.poisoned=True
            finally:
                if token is not None:SCOPE.reset(token)
                if acquired:self.service.lock.release()

async def prepare_speech_worker(speech):
    """Call the live speech client's own ASR process before pipeline/audio start."""
    result=None
    session=speech.sessions['asr'];speech.events.emit('assistant_asr_worker_readiness_start',provider_calls=0,audio_opened=False)
    try:
        info=SpeechClients.unpack(await session.call_tool('server_info',{}))
        result=SpeechClients.unpack(await session.call_tool('network_readiness',{},read_timeout_seconds=timedelta(seconds=30)))
        if (result.get('ok') is not True or result.get('network_ready') is not True or result.get('server_pid')!=info.get('server_pid')
            or result.get('server_role')!='asr' or result.get('provider')!='groq'
            or result.get('hostname_TLS_verified') is not True or result.get('connector_reused_for_transcription') is not True
            or any(type(result.get(k)) is not int or result[k]!=0 for k in ('http_requests','model_requests','audio_bytes'))):
            raise SpeechServiceFailed('actual_asr_worker_readiness_failed')
        speech.events.emit('assistant_asr_worker_ready',server_pid=result['server_pid'],connection_epoch=result['connection_epoch'],
                           dns_seconds=result['dns_seconds'],connection_seconds=result['connection_seconds'],
                           dns_cache_seconds=result['dns_cache_seconds'],keepalive_seconds=result['keepalive_seconds'],HTTP_requests=0,model_requests=0,audio_opened=False)
        return result
    except asyncio.CancelledError:raise
    except Exception:
        stage=result.get('stage') if isinstance(result,dict) else None
        kind=result.get('error_type') if isinstance(result,dict) else None
        if stage not in {'dns','tcp_tls','initialization'}:stage='unknown'
        if kind not in {'TimeoutError','ConnectionTimeoutError','ClientConnectorError','ClientConnectorDNSError','ClientConnectorCertificateError','ClientConnectorSSLError','ValueError'}:kind='unclassified'
        speech.events.emit('assistant_asr_worker_readiness_failed',stage=stage,error_type=kind,automatic_retry=False,audio_opened=False);raise

