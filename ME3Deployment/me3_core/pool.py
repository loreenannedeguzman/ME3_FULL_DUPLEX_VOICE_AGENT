"""One reusable successful ASR connection; failures retire it without replay."""
import asyncio,contextvars,time,uuid,json
from pathlib import Path
from dataclasses import dataclass
from me3_m7.provider import TracedGoogle

@dataclass
class RequestScope:
    emit:object
    wire:int=0
SCOPE=contextvars.ContextVar('me3_core_request_scope',default=None)

def dispatch(event,**fields):
    scope=SCOPE.get()
    if scope is not None:scope.emit(event,**fields)

class ScopedGoogle(TracedGoogle):
    # The pinned SDK hook's one-wire counter belongs to the request context.
    @property
    def wire(self):
        scope=SCOPE.get();return scope.wire if scope is not None else 0
    @wire.setter
    def wire(self,value):
        scope=SCOPE.get()
        if scope is not None:scope.wire=value
    def __init__(self,emit):
        self.owned_session=None
        super().__init__(emit)
        api=self.provider.client._api_client
        if api._use_aiohttp():
            import aiohttp
            config=json.loads((Path(__file__).resolve().parents[1]/'config/core_agent.json').read_text(encoding='utf-8'))
            keepalive=config['http_keepalive_seconds'];dns=config['dns_cache_seconds']
            if type(keepalive) is not int or type(dns) is not int or not 15<=keepalive<=120 or not 10<=dns<=300:raise ValueError('invalid_connection_cache_bounds')
            # Pinned SDK exposes this documented HttpOptions field; caller owns its lifecycle.
            connector=aiohttp.TCPConnector(limit=1,keepalive_timeout=keepalive,ttl_dns_cache=dns)
            self.owned_session=aiohttp.ClientSession(connector=connector,trust_env=True)
            api._http_options.aiohttp_client=self.owned_session
            emit('core_connection_policy',keepalive_seconds=keepalive,dns_cache_seconds=dns,max_connections=1,tls_verification_unchanged=True)
            original=api._get_aiohttp_session
            async def session():
                value=await original()
                if not getattr(value,'_me3_core_reuse_trace',False):
                    import aiohttp
                    trace=aiohttp.TraceConfig()
                    async def reused(*_):dispatch('core_transport_connection_reused')
                    trace.on_connection_reuseconn.append(reused);trace.freeze();value.trace_configs.append(trace);value._me3_core_reuse_trace=True
                return value
            api._get_aiohttp_session=session
    async def close(self):
        try:await super().close()
        finally:
            if self.owned_session is not None and not self.owned_session.closed:await self.owned_session.close()

class RequestPool:
    def __init__(self,factory=ScopedGoogle,mode='reuse_after_success'):
        if mode not in {'reuse_after_success','per_request'}:raise ValueError('explicit_connection_mode_required')
        self.factory=factory;self.mode=mode;self.cached=None;self.active=None;self.closed=False;self.poisoned=False;self.epoch=0;self.uses=0
    def lease(self,emit):
        if self.closed or self.poisoned:raise ValueError('asr_pool_unavailable')
        if self.active is not None:raise ValueError('asr_pool_already_leased')
        lease=Lease(self,emit);self.active=lease;return lease
    async def shutdown(self):
        self.closed=True
        if self.active is not None:raise ValueError('asr_pool_shutdown_while_leased')
        provider=self.cached;self.cached=None
        if provider is not None:await provider.close()

class Lease:
    def __init__(self,pool,emit):self.pool=pool;self.emit=emit;self.scope=RequestScope(emit);self.provider=None;self.success=False;self.finished=False;self.called=False
    async def transcribe(self,pcm,profile,previous,audio_format='wav'):
        if self.called or self.finished:raise ValueError('lease_single_request_only')
        self.called=True;token=SCOPE.set(self.scope)
        try:
            pool=self.pool;reused=pool.cached is not None
            if not reused:
                pool.cached=pool.factory(dispatch);pool.epoch+=1;pool.uses=0
            self.provider=pool.cached
            self.emit('core_pool_borrow',epoch=pool.epoch,reused=reused,reuse_index=pool.uses,mode=pool.mode)
            result=await self.provider.transcribe(pcm,profile,previous,audio_format)
            self.success=True;return result
        except BaseException:
            self.success=False;raise
        finally:SCOPE.reset(token)
    async def close(self):
        if self.finished:return
        self.finished=True;pool=self.pool;token=SCOPE.set(self.scope)
        try:
            if self.success and pool.mode=='reuse_after_success' and not pool.closed:
                pool.uses+=1;self.emit('core_pool_released_idle',epoch=pool.epoch,reuse_index=pool.uses)
            else:
                provider=pool.cached;pool.cached=None
                self.emit('core_pool_retire_start',epoch=pool.epoch,completed=self.success)
                if provider is not None:await provider.close()
                self.emit('core_pool_retired',epoch=pool.epoch)
        except BaseException:
            pool.poisoned=True;raise
        finally:
            pool.active=None;SCOPE.reset(token)
