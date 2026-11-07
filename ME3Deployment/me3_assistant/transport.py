"""Normal-only bounded connection creation; no retry, provider or TLS policy change.

The pinned aiohttp connector hook includes DNS/TCP/TLS establishment. An already
pooled connection does not take this path. This is a project recovery policy,
not a professor-specified response latency threshold.
"""
import asyncio,math
from me3_core.pool import ScopedGoogle

CONNECT_SECONDS=3.0
ADDRESS_SECONDS=1.0
# Cold Pi NSS resolution takes about6s before TCP starts. Reasoning may wait
# longer for connection creation, within its unchanged30s total request budget.
# Groq ASR has the same measured cold resolver path. Keep the default Google
# connection window unchanged; no DNS server, address or TLS override.
REASONING_CONNECT_SECONDS=10.0
ASR_CONNECT_SECONDS=10.0
ASR_ADDRESS_SECONDS=4.0

def connection_timeouts(provider):
    connector=getattr(getattr(provider,'owned_session',None),'connector',None)
    return getattr(connector,'_me3_normal_connect_timeouts',0)

def bind_connection_deadline(provider,seconds=CONNECT_SECONDS,*,address_seconds=None):
    if type(seconds) not in (int,float) or not math.isfinite(seconds) or not 0<seconds<=REASONING_CONNECT_SECONDS:raise ValueError('invalid_normal_connection_deadline')
    if address_seconds is None:address_seconds=min(ADDRESS_SECONDS,seconds/3)
    if type(address_seconds) not in (int,float) or not math.isfinite(address_seconds) or not 0<address_seconds<=seconds:raise ValueError('invalid_normal_address_deadline')
    connector=getattr(getattr(provider,'owned_session',None),'connector',None)
    if connector is None or not callable(getattr(connector,'_create_connection',None)):raise ValueError('normal_aiohttp_connector_required')
    existing=getattr(connector,'_me3_normal_connect_seconds',None)
    if existing is not None:
        if existing!=seconds or connector._me3_normal_address_seconds!=address_seconds:raise ValueError('normal_connection_deadline_already_bound')
        return
    original=connector._create_connection;emit=provider.emit
    connector._me3_normal_connect_seconds=seconds;connector._me3_normal_connect_timeouts=0
    connector._me3_normal_address_seconds=address_seconds
    connector._me3_normal_address_timeouts=0
    original_address=getattr(connector,'_wrap_create_connection',None)
    if callable(original_address):
        async def address(*args,**kwargs):
            timer=asyncio.timeout(address_seconds)
            emit('normal_address_connect_start',address_seconds=address_seconds,address_count=len(kwargs.get('addr_infos',())),http_replay=False)
            try:
                async with timer:result=await original_address(*args,**kwargs)
            except TimeoutError:
                if timer.expired():
                    connector._me3_normal_address_timeouts+=1
                    emit('normal_address_connect_timeout',address_seconds=address_seconds,automatic_retry=False,http_replay=False)
                raise
            except asyncio.CancelledError:
                emit('normal_address_connect_cancelled',automatic_retry=False,http_replay=False);raise
            emit('normal_address_connect_end',http_replay=False);return result
        # aiohttp's existing direct-connection loop may advance to an alternate
        # resolved address before any HTTP request is sent. Keep that loop,
        # original TLS verification/arguments, and the outer total deadline.
        connector._wrap_create_connection=address
    async def bounded(*args,**kwargs):
        timer=asyncio.timeout(seconds);before=connector._me3_normal_address_timeouts
        try:
            async with timer:return await original(*args,**kwargs)
        except asyncio.CancelledError:
            emit('normal_connection_creation_cancelled',automatic_retry=False);raise
        except TimeoutError:
            if timer.expired():
                connector._me3_normal_connect_timeouts+=1
                emit('normal_connection_deadline',connect_seconds=seconds,automatic_retry=False,scope='DNS/TCP/TLS creation only; not response-body deadline')
            elif connector._me3_normal_address_timeouts>before:
                connector._me3_normal_connect_timeouts+=1
                emit('normal_connection_addresses_exhausted',connect_seconds=seconds,address_seconds=address_seconds,automatic_retry=False,http_replay=False)
            raise
    connector._create_connection=bounded

class BoundedScopedGoogle(ScopedGoogle):
    def __init__(self,emit):
        super().__init__(emit);bind_connection_deadline(self)
    async def transcribe(self,*args,**kwargs):
        before=connection_timeouts(self)
        try:return await super().transcribe(*args,**kwargs)
        except Exception:
            # The pinned SDK/aiohttp may wrap a connector timeout. Preserve the
            # existing MCP TimeoutError recovery contract only when OUR deadline
            # actually fired. Auth/quota/recognition errors are never relabeled.
            if connection_timeouts(self)>before:raise TimeoutError('normal_connection_deadline') from None
            raise
