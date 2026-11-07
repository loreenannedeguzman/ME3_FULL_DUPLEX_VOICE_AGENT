"""Candidate ASR socket policy; retain the student MCP, model and request guards.

Reuse the actual worker's session, DNS cache and validated address handling, but
never upload audio over an idle pooled TCP connection. Readiness closes its
probe socket. Each distinct user request makes exactly one fresh-connection
POST, with the existing admission, cancellation, TLS and total time limits.
"""
import argparse,asyncio,ipaddress,json,logging,math,socket,time
from pathlib import Path
import aiohttp
from me3_assistant import groq_backend as backend

ASR_NATIVE_CONNECT_SECONDS=20
ASR_NATIVE_SOCKET_SECONDS=10

def dns_seconds():
    cfg=json.loads((Path(__file__).resolve().parents[1]/'config/core_agent.json').read_bytes())
    ttl=cfg['dns_cache_seconds']
    if type(ttl) is not int or not 10<=ttl<=300:raise ValueError('invalid_connection_cache_bounds')
    return ttl

class WorkerDNS:
    """Worker-only positive DNS cache; original expiry never slides on reuse."""
    def __init__(self,ttl,*,resolver=None,clock=time.monotonic):
        if type(ttl) is not int or not 10<=ttl<=300:raise ValueError('invalid_connection_cache_bounds')
        self.ttl=ttl;self.resolver=resolver;self.clock=clock;self.record=None;self.closed=False;self.leases=set()
    def lease(self):
        if self.closed:raise ValueError('ASR_DNS_worker_closed')
        lease=DNSLease(self);self.leases.add(lease);return lease
    async def resolve(self,lease,host,port,family):
        if self.closed or lease.closed:raise asyncio.CancelledError()
        lease.answer_record=None
        approved=(host=='api.groq.com' and type(port) is int and port==443 and family==socket.AF_INET)
        now=self.clock()
        if not math.isfinite(now):raise ValueError('invalid_ASR_DNS_clock')
        if self.record is not None:
            born,expires,records=self.record
            if now<born or now>=expires:self.record=None
            elif approved:
                lease.answer_record=self.record;return [dict(row) for row in records]
        if self.resolver is None:self.resolver=aiohttp.resolver.DefaultResolver()
        records=await self.resolver.resolve(host,port,family)
        if self.closed or lease.closed or asyncio.current_task().cancelling():raise asyncio.CancelledError()
        if not records:raise OSError('empty_ASR_DNS_resolution')
        # Public ResolveResult fields only; never accept an alias/wrong family
        # as a positive answer for the approved endpoint.
        if approved:
            clean=[]
            for row in records:
                if (not isinstance(row,dict) or row.get('hostname')!=host or row.get('port')!=port
                    or row.get('family')!=family or not isinstance(row.get('host'),str)
                    or ipaddress.ip_address(row['host']).version!=4):raise ValueError('invalid_ASR_DNS_answer')
                clean.append(dict(row))
            finished=self.clock()
            if not math.isfinite(finished) or finished<now:raise ValueError('invalid_ASR_DNS_clock')
            self.record=(finished,finished+self.ttl,tuple(clean));lease.answer_record=self.record
        return [dict(row) for row in records]
    def invalidate_failed_connection(self,lease):
        # Only the exact positive answer used by this live connector lease;
        # another lease's later resolution must never be invalidated here.
        if (self.closed or lease.closed or lease not in self.leases or self.record is None
            or lease.answer_record is not self.record):return False
        self.record=None;lease.answer_record=None;return True
    async def close(self):
        if self.closed:return
        self.closed=True;self.record=None
        for lease in tuple(self.leases):await lease.close()
        if self.resolver is not None:await self.resolver.close()

class DNSLease(aiohttp.abc.AbstractResolver):
    """A fresh connector lease can be retired without destroying worker DNS."""
    def __init__(self,worker):self.worker=worker;self.closed=False;self.tasks=set();self.answer_record=None
    async def resolve(self,host,port=0,family=socket.AF_INET):
        if self.closed:raise asyncio.CancelledError()
        task=asyncio.current_task();self.tasks.add(task)
        try:return await self.worker.resolve(self,host,port,family)
        finally:self.tasks.discard(task)
    async def close(self):
        if self.closed:return
        self.closed=True;self.worker.leases.discard(self)
        tasks=tuple(t for t in self.tasks if t is not asyncio.current_task())
        for task in tasks:task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)

class FreshTransport(backend.Transport):
    def __init__(self,*args,dns=None,**kwargs):
        super().__init__(*args,**kwargs);self.worker_dns=dns;self.dns_lease=None;self.owns_dns=dns is None
    async def prepare_dns(self):
        if not self.owned or not self.active:raise ValueError('owned_active_asr_transport_required')
        self.ensure_session('asr');start=time.monotonic()
        from me3_assistant.worker_readiness import DNS_SECONDS
        from yarl import URL
        endpoint=URL(backend.ENDPOINTS['asr'])
        if endpoint.scheme!='https' or endpoint.host!='api.groq.com' or endpoint.port!=443:raise ValueError('asr_readiness_requires_provider_https')
        hit=self.worker_dns.record is not None and self.worker_dns.record[0]<=self.worker_dns.clock()<self.worker_dns.record[1]
        self.emit('groq_asr_prewire_dns_start',wire_attempts=0,automatic_retry=False)
        try:
            async with asyncio.timeout(DNS_SECONDS):await self.dns_lease.resolve(endpoint.host,443,backend.GROQ_CONNECTION_FAMILY)
            self.emit('groq_asr_prewire_dns_ready',seconds=round(time.monotonic()-start,4),cache_hit=hit,worker_scoped=True,wire_attempts=0,automatic_retry=False)
        except BaseException as exc:
            self.emit('groq_asr_prewire_dns_failed',stage='dns',error_type=type(exc).__name__,wire_attempts=0,automatic_retry=False);raise
    async def close(self):
        try:
            if self.dns_lease is not None:await self.dns_lease.close()
        finally:
            try:await super().close()
            finally:
                if self.owns_dns and self.worker_dns is not None:await self.worker_dns.close()
    def ensure_session(self,role):
        if role!='asr':raise ValueError('candidate_asr_transport_role_required')
        if self.session is None:
            dns=dns_seconds()
            if self.worker_dns is None:self.worker_dns=WorkerDNS(dns)
            if self.worker_dns.ttl!=dns:raise ValueError('ASR_DNS_configuration_changed')
            self.dns_lease=self.worker_dns.lease()
            connector=aiohttp.TCPConnector(family=backend.GROQ_CONNECTION_FAMILY,limit=1,force_close=True,keepalive_timeout=None,use_dns_cache=False,ttl_dns_cache=dns,resolver=self.dns_lease)
            trace=aiohttp.TraceConfig()
            async def started(session,context,params):
                context.asr_connection_started=False;context.asr_connection_ready=False;context.asr_headers_sent=False
            async def connecting(session,context,params):context.asr_connection_started=True
            async def connected(session,context,params):context.asr_connection_ready=True
            async def headers_sent(session,context,params):context.asr_headers_sent=True
            async def failed(session,context,params):
                number=getattr(params.exception,'errno',None)
                self.emit('candidate_asr_socket_error',error_type=type(params.exception).__name__,os_errno=number if type(number) is int else None,exception_text_retained=False,automatic_retry=False)
                task=asyncio.current_task()
                if (isinstance(params.exception,aiohttp.ConnectionTimeoutError)
                    and getattr(context,'asr_connection_started',False) is True
                    and getattr(context,'asr_connection_ready',False) is False
                    and getattr(context,'asr_headers_sent',False) is False
                    and not (task and task.cancelling())
                    and not (self.stop_requested is not None and self.stop_requested.is_set())
                    and self.worker_dns.invalidate_failed_connection(self.dns_lease)):
                    self.emit('candidate_asr_failed_connection_DNS_invalidated',scope='next distinct user request only',reason='pre_HTTP_connection_timeout',automatic_retry=False,HTTP_replay=False)
            trace.on_request_start.append(started)
            trace.on_connection_create_start.append(connecting)
            trace.on_connection_create_end.append(connected)
            trace.on_request_headers_sent.append(headers_sent)
            trace.on_request_exception.append(failed)
            self.session=aiohttp.ClientSession(connector=connector,timeout=aiohttp.ClientTimeout(total=30,connect=ASR_NATIVE_CONNECT_SECONDS,sock_connect=ASR_NATIVE_SOCKET_SECONDS),trust_env=True,trace_configs=[backend.connection_trace(self.emit),trace])
            self.emit('candidate_asr_connection_policy',native_aiohttp_timeouts=True,connect_seconds=ASR_NATIVE_CONNECT_SECONDS,socket_connect_seconds=ASR_NATIVE_SOCKET_SECONDS,total_request_seconds=30,private_connector_hooks=False,address_family='IPv4',force_close=True,readiness_socket_reused=False,dns_cache_seconds=dns,system_network_settings_changed=False,pinned_IP=False,TLS_verification=True,automatic_retry=False)
        if self.session.closed:raise ValueError('groq_transport_closed')
        return self.session

class FreshASR(backend.GroqASR):
    def __init__(self,emit,*,transport=None,dns=None):
        if transport is None:
            from me3_assistant.backends import groq_key
            transport=FreshTransport(emit,key=groq_key(),opt_in=True,dns=dns)
        super().__init__(emit,transport=transport)
    async def prepare_connection(self):
        result=await super().prepare_connection()
        # The shared connector/DNS are prepared; force_close already disposed
        # of the verified probe socket when it was released, before any audio.
        return {**result,'readiness_socket_reused':False,'request_connection_policy':'fresh_TCP_TLS_per_distinct_request','keepalive_seconds':None}

def make_server():
    from me3_assistant.groq_server import make_server as student_server
    from me3_core.pool import RequestPool
    dns=WorkerDNS(dns_seconds())
    class WorkerPool(RequestPool):
        async def shutdown(self):
            try:await super().shutdown()
            finally:await dns.close()
    return student_server(WorkerPool(factory=lambda emit:FreshASR(emit,dns=dns),mode='reuse_after_success'))

def main():
    p=argparse.ArgumentParser();p.add_argument('role',choices=['asr','tts']);args=p.parse_args();logging.disable(logging.CRITICAL)
    if args.role=='tts':
        from speech_mcp.server import make_server as student_piper
        server=student_piper('tts')
    else:server=make_server()
    server.run(transport='stdio')

if __name__=='__main__':main()
