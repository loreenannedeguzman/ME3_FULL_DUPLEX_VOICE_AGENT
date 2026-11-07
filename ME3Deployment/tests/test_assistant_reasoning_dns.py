"""Owned Groq connection windows; default/TLS/total and no-retry policy."""
import asyncio,socket,unittest
from types import SimpleNamespace
from unittest.mock import patch
import aiohttp
from me3_assistant.transport import bind_connection_deadline,REASONING_CONNECT_SECONDS,CONNECT_SECONDS,ADDRESS_SECONDS
from me3_assistant.groq_backend import Transport
from me3_assistant.admission import Admission

class DelayedDNS(aiohttp.abc.AbstractResolver):
    def __init__(self,port):self.port=port;self.calls=0
    async def resolve(self,host,port=0,family=socket.AF_INET):
        self.calls+=1;await asyncio.sleep(3.2)
        return [{'hostname':host,'host':'127.0.0.1','port':self.port,'family':socket.AF_INET,'proto':socket.IPPROTO_TCP,'flags':socket.AI_NUMERICHOST}]
    async def close(self):pass

class Policy(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):self.events=[];self.requests=0;self.handlers=set()
    async def asyncTearDown(self):
        self.server.close();await self.server.wait_closed()
        if self.handlers:await asyncio.gather(*list(self.handlers))
    def emit(self,event,**kw):self.events.append({'event':event,**kw})
    async def peer(self):
        async def handle(reader,writer):
            task=asyncio.current_task();self.handlers.add(task)
            try:
                await reader.readuntil(b'\r\n\r\n');self.requests+=1
                body=b'{"choices":[]}';writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: '+str(len(body)).encode()+b'\r\nConnection: close\r\n\r\n'+body);await writer.drain()
            except (ConnectionError,asyncio.IncompleteReadError):pass
            finally:
                writer.close();await writer.wait_closed();self.handlers.discard(task)
        self.server=await asyncio.start_server(handle,'127.0.0.1',0);return self.server.sockets[0].getsockname()[1]
    async def test_actual_owned_reasoning_request_survives_dns_over_three_seconds(self):
        port=await self.peer();resolver=DelayedDNS(port);original=aiohttp.TCPConnector
        transport=Transport(self.emit,key='disposable-public-fixture',opt_in=True,admission=Admission())
        def connector(*args,**kw):return original(*args,resolver=resolver,**kw)
        try:
            with patch('aiohttp.TCPConnector',connector),patch.dict('me3_assistant.groq_backend.ENDPOINTS',{'reasoning':'http://delayed.fixture.invalid/'}):
                result=await transport.request('reasoning',json={'max_completion_tokens':16})
            self.assertEqual(result,{'choices':[]});self.assertEqual(resolver.calls,1);self.assertEqual(self.requests,1)
            self.assertEqual(transport.session.timeout.total,30);self.assertEqual(transport.session.timeout.connect,10)
            self.assertEqual(transport.session.connector._me3_normal_connect_seconds,10)
            self.assertTrue(transport.session.connector._ssl)
            self.assertEqual(sum(e['event']=='groq_request_dispatch' for e in self.events),1)
        finally:await transport.close()
    async def test_owned_asr_uses_explicit_cold_dns_budget(self):
        # The owned ASR path now requires HTTPS before prewire resolution.
        # Reuse a trusted local TLS fixture, never an external DNS provider.
        from tests.test_assistant_worker_readiness import TLSPreparationTests,Resolver
        fixture=TLSPreparationTests('test_prepares_zero_HTTP_then_same_session_sends_one_post_without_second_DNS_or_TLS')
        await fixture.asyncSetUp();await self.peer()
        resolver=Resolver(fixture.port);transport,connector,_=fixture.transport(resolver)
        import os
        try:
            with patch('me3_assistant.groq_backend.groq_connector',return_value=connector),patch.dict('me3_assistant.groq_backend.ENDPOINTS',{'asr':'https://me3-fixture.invalid/'}),patch.dict(os.environ,{'NO_PROXY':'me3-fixture.invalid','no_proxy':'me3-fixture.invalid'}):
                await transport.request('asr',data='fixture')
            self.assertEqual(transport.session.timeout.total,30);self.assertEqual(transport.session.timeout.connect,10)
            self.assertEqual(transport.session.connector._me3_normal_connect_seconds,10)
            self.assertEqual((fixture.posts,resolver.calls),(1,1))
        finally:await transport.close();await fixture.asyncTearDown()
    async def test_default_policy_unchanged_and_reasoning_timeout_cleans_up_without_retry(self):
        await self.peer();self.assertEqual(CONNECT_SECONDS,3);self.assertEqual(ADDRESS_SECONDS,1);self.assertEqual(REASONING_CONNECT_SECONDS,10)
        calls=0;closed=asyncio.Event()
        async def blocked():
            nonlocal calls;calls+=1
            try:await asyncio.Future()
            finally:closed.set()
        connector=SimpleNamespace(_create_connection=blocked)
        provider=SimpleNamespace(owned_session=SimpleNamespace(connector=connector),emit=self.emit)
        bind_connection_deadline(provider,.03)
        with self.assertRaises(TimeoutError):await connector._create_connection()
        self.assertTrue(closed.is_set());self.assertEqual(calls,1)

if __name__=='__main__':unittest.main()
