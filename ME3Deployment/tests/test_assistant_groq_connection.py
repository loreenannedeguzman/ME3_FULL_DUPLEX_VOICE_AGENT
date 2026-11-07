"""Actual localhost TLS peers; Groq's owned client, no external APIs/audio."""
import asyncio,json,ssl,unittest
from unittest.mock import patch
import aiohttp
from me3_assistant import groq_backend as g
from tests import test_assistant_address_fallback as fixture

class GroqConnectionTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixture.AddressFallbackTests.asyncSetUp
    asyncTearDown=fixture.AddressFallbackTests.asyncTearDown
    peer=fixture.AddressFallbackTests.peer
    async def json_peer(self,status=200):
        async def handler(reader,writer):
            task=asyncio.current_task();self.handlers.add(task);self.connections+=1
            try:
                await reader.readuntil(b'\r\n\r\n');self.http_requests+=1
                body=json.dumps({'fixture':True}).encode()
                writer.write(f'HTTP/1.1 {status} Fixture\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n'.encode()+body);await writer.drain()
            finally:
                writer.close()
                try:await writer.wait_closed()
                except (ConnectionError,ssl.SSLError):pass
                self.handlers.discard(task)
        server=await asyncio.start_server(handler,'127.0.0.1',0,ssl=self.server_ssl);self.servers.append(server);return server.sockets[0].getsockname()[1]
    async def transport(self,ports,operation):
        connector=aiohttp.TCPConnector(resolver=fixture.Resolver(ports),ssl=self.client_ssl)
        session=aiohttp.ClientSession(connector=connector,timeout=aiohttp.ClientTimeout(total=30,connect=10));events=[]
        transport=g.Transport(lambda event,**kw:events.append((event,kw)),key='PRIVATE_FIXTURE_KEY',opt_in=True,admission=g.Admission())
        try:
            with patch.object(g.aiohttp,'ClientSession',return_value=session),patch.dict(g.ENDPOINTS,{'reasoning':'https://me3-fixture.invalid/','asr':'https://me3-fixture.invalid/'}):await operation(transport,connector,events)
        finally:await transport.close();await session.close()
        self.assertTrue(connector.closed);self.assertFalse(transport.active);self.assertNotIn('PRIVATE_FIXTURE_KEY',str(events))
    async def test_stalled_first_tls_address_advances_before_one_http_post(self):
        stalled=await self.peer();good=await self.json_peer()
        async def check(t,connector,events):
            self.assertEqual(await t.request('reasoning',json={'fixture':True}),{'fixture':True})
            self.assertEqual((self.connections,self.http_requests),(2,1));self.assertFalse(connector._acquired)
            self.assertTrue(any(name=='normal_address_connect_timeout' for name,_ in events))
            self.assertEqual(len([e for e in events if e[0]=='groq_request_dispatch']),1)
        await self.transport([stalled,good],check)
    async def test_all_tls_addresses_stalled_stay_bounded_and_send_no_http(self):
        ports=[await self.peer(),await self.peer()]
        async def check(t,connector,events):
            start=asyncio.get_running_loop().time()
            with self.assertRaises(TimeoutError):await t.request('reasoning',json={})
            self.assertLess(asyncio.get_running_loop().time()-start,3.5)
            self.assertEqual((self.connections,self.http_requests),(2,0));self.assertFalse(connector._acquired)
        await self.transport(ports,check)
    async def test_user_cancel_during_first_tls_does_not_fall_back_or_send_http(self):
        stalled=await self.peer();good=await self.json_peer()
        async def check(t,connector,events):
            task=asyncio.create_task(t.request('reasoning',json={}));await self.entered.wait();task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
            self.assertEqual((self.connections,self.http_requests),(1,0));self.assertFalse(connector._acquired)
        await self.transport([stalled,good],check)
    async def test_quota_error_after_connection_fallback_is_not_relabelled_or_replayed(self):
        stalled=await self.peer();good=await self.json_peer(429)
        async def check(t,connector,events):
            with self.assertRaises(g.ProviderError) as caught:await t.request('reasoning',json={})
            self.assertEqual(caught.exception.code,429);self.assertEqual(self.http_requests,1)
            self.assertEqual(len([e for e in events if e[0]=='groq_request_dispatch']),1)
        await self.transport([stalled,good],check)
    async def test_asr_owned_client_uses_the_same_pre_http_fallback_without_audio_devices(self):
        stalled=await self.peer();good=await self.json_peer()
        async def check(t,connector,events):
            self.assertEqual(await t.request('asr',data=b'fixture'),{'fixture':True})
            self.assertEqual((self.connections,self.http_requests),(2,1))
            self.assertTrue(any(name=='normal_address_connect_timeout' for name,_ in events))
        await self.transport([stalled,good],check)

if __name__=='__main__':unittest.main()
