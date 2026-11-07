"""Local DNS/pool/HTTP lifecycle evidence, not model calls or production keys."""
import asyncio,json,socket,unittest
from unittest.mock import patch
import aiohttp
from me3_assistant.groq_backend import connection_trace,Transport
from me3_assistant.admission import Admission

class SlowDNS(aiohttp.abc.AbstractResolver):
    async def resolve(self,*args,**kw):await asyncio.Future()
    async def close(self):pass

class NetworkStageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):self.events=[];self.handlers=set();self.release=asyncio.Event();self.servers=[];self.http_count=0
    async def asyncTearDown(self):
        self.release.set()
        for server in self.servers:server.close();await server.wait_closed()
        if self.handlers:await asyncio.gather(*list(self.handlers),return_exceptions=True)
    def emit(self,event,**kw):self.events.append({'event':event,**kw})
    async def peer(self,wait=False):
        async def handle(reader,writer):
            task=asyncio.current_task();self.handlers.add(task)
            try:
                await reader.readuntil(b'\r\n\r\n');self.http_count+=1
                if wait:await self.release.wait()
                else:
                    body=b'{"choices":[]}';writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: '+str(len(body)).encode()+b'\r\nConnection: close\r\n\r\n'+body);await writer.drain()
            except (ConnectionError,asyncio.IncompleteReadError):pass
            finally:
                writer.close()
                try:await writer.wait_closed()
                except ConnectionError:pass
                self.handlers.discard(task)
        server=await asyncio.start_server(handle,'127.0.0.1',0);self.servers.append(server);return 'http://127.0.0.1:'+str(server.sockets[0].getsockname()[1])+'/'
    async def test_client_connect_timeout_has_dns_stage_and_no_http(self):
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(resolver=SlowDNS()),timeout=aiohttp.ClientTimeout(total=1,connect=.2),trace_configs=[connection_trace(self.emit)]) as session:
            with self.assertRaises(aiohttp.ConnectionTimeoutError):await session.get('http://unresolved.fixture.invalid/')
            error=self.events[-1];self.assertEqual(error['stage'],'dns');self.assertEqual(error['error_type'],'ConnectionTimeoutError')
            self.assertNotIn('groq_network_http_headers_sent',[e['event'] for e in self.events]);self.assertFalse(session.connector._acquired)
    async def test_response_timeout_is_distinct_and_sensitive_headers_never_logged(self):
        url=await self.peer(wait=True)
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=1,sock_read=.2),trace_configs=[connection_trace(self.emit)]) as session:
            with self.assertRaises(aiohttp.SocketTimeoutError):await session.get(url,headers={'Authorization':'Bearer public-disposable-fixture'})
        error=self.events[-1];self.assertEqual(error['stage'],'http_response');self.assertEqual(self.http_count,1)
        serialized=json.dumps(self.events);self.assertNotIn('public-disposable-fixture',serialized);self.assertNotIn('127.0.0.1',serialized);self.assertNotIn('Authorization',serialized)
    async def test_pool_wait_timeout_is_distinct_from_dns_and_tls(self):
        url=await self.peer(wait=True)
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=1),timeout=aiohttp.ClientTimeout(total=1,connect=.2),trace_configs=[connection_trace(self.emit)]) as session:
            first=asyncio.create_task(session.get(url))
            while not self.http_count:await asyncio.sleep(.01)
            with self.assertRaises(aiohttp.ConnectionTimeoutError):await session.get(url)
            self.assertEqual(self.events[-1]['stage'],'pool_wait');self.assertEqual(self.http_count,1)
            first.cancel();await asyncio.gather(first,return_exceptions=True)
    async def test_actual_owned_transport_installs_trace_and_preserves_timeout_policy(self):
        url=await self.peer();transport=Transport(self.emit,key='public-disposable-fixture',opt_in=True,admission=Admission())
        try:
            with patch.dict('me3_assistant.groq_backend.ENDPOINTS',{'reasoning':url}):result=await transport.request('reasoning',json={'max_completion_tokens':16})
            self.assertEqual(result,{'choices':[]});self.assertEqual(transport.session.timeout.total,30);self.assertEqual(transport.session.timeout.connect,10)
            names=[e['event'] for e in self.events];self.assertIn('groq_network_connection_ready',names);self.assertIn('groq_network_http_headers_sent',names);self.assertEqual(self.http_count,1)
            self.assertNotIn('public-disposable-fixture',json.dumps(self.events))
        finally:await transport.close()

if __name__=='__main__':unittest.main()
