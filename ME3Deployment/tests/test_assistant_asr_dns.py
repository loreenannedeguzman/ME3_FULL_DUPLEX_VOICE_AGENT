"""Synthetic audio/local HTTP only: slow DNS through actual Groq ASR service."""
import asyncio,base64,json,socket,ssl,tempfile,unittest
from pathlib import Path
from tests.test_assistant_address_fallback import CERTIFICATE,TEST_PRIVATE_KEY
from unittest.mock import patch
import aiohttp
from me3_assistant.admission import Admission
from me3_assistant.groq_backend import GroqASR,Transport
from me3_assistant.groq_server import Service
from me3_core.pool import RequestPool
from me3_assistant.transport import CONNECT_SECONDS,ADDRESS_SECONDS,ASR_CONNECT_SECONDS

class Resolver(aiohttp.abc.AbstractResolver):
    def __init__(self,port,delay=None):self.port=port;self.delay=delay;self.calls=0;self.entered=asyncio.Event()
    async def resolve(self,host,port=0,family=socket.AF_INET):
        self.calls+=1;self.entered.set()
        if self.delay is None:await asyncio.Future()
        else:await asyncio.sleep(self.delay)
        return [{'hostname':host,'host':'127.0.0.1','port':self.port,'family':socket.AF_INET,'proto':socket.IPPROTO_TCP,'flags':socket.AI_NUMERICHOST}]
    async def close(self):pass

class ASRDNS(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.events=[];self.requests=[];self.tasks=set()
        self.temp=tempfile.TemporaryDirectory();p=Path(self.temp.name);(p/'cert').write_text(CERTIFICATE);(p/'key').write_text(TEST_PRIVATE_KEY)
        self.server_ssl=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);self.server_ssl.load_cert_chain(p/'cert',p/'key')
        self.client_ssl=ssl.create_default_context(cadata=CERTIFICATE)
        async def peer(reader,writer):
            task=asyncio.current_task();self.tasks.add(task)
            try:
                head=await reader.readuntil(b'\r\n\r\n');headers={k.lower():v.strip() for k,v in [line.split(b':',1) for line in head.split(b'\r\n')[1:] if b':' in line]}
                body=await reader.readexactly(int(headers[b'content-length']));self.requests.append(body)
                response=json.dumps({'text':'slow DNS fixture'}).encode();writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: '+str(len(response)).encode()+b'\r\nConnection: close\r\n\r\n'+response);await writer.drain()
            except (ConnectionError,asyncio.IncompleteReadError):pass
            finally:
                writer.close();await writer.wait_closed();self.tasks.discard(task)
        self.server=await asyncio.start_server(peer,'127.0.0.1',0,ssl=self.server_ssl);self.port=self.server.sockets[0].getsockname()[1]
    async def asyncTearDown(self):
        self.server.close();await self.server.wait_closed()
        if self.tasks:await asyncio.gather(*list(self.tasks))
        self.temp.cleanup()
    def emit(self,event,**kw):self.events.append({'event':event,**kw})
    def setup_provider(self,resolver):
        self.transport=Transport(self.emit,key='disposable-fixture',opt_in=True,admission=Admission())
        original=aiohttp.TCPConnector
        def connector(*args,**kw):return original(*args,resolver=resolver,ssl=self.client_ssl,**kw)
        return patch('aiohttp.TCPConnector',connector),patch.dict('me3_assistant.groq_backend.ENDPOINTS',{'asr':'https://me3-fixture.invalid/'})
    async def test_service_and_reusable_pool_complete_one_post_after_six_second_dns(self):
        resolver=Resolver(self.port,6.2);connector,endpoint=self.setup_provider(resolver)
        pool=RequestPool(factory=lambda emit:GroqASR(emit,transport=self.transport),mode='reuse_after_success')
        service=Service(pool.lease,writer=self.emit)
        try:
            with connector,endpoint:
                result=await service.call(base64.b64encode(bytes(640)).decode(),1,'asr-dns-success')
            self.assertTrue(result['ok']);self.assertTrue(result['cleanup_ok']);self.assertEqual(result['text'],'slow DNS fixture')
            self.assertEqual(result['provider'],'groq');self.assertEqual(service.states['asr-dns-success']['state'],'released')
            self.assertEqual(resolver.calls,1);self.assertEqual(len(self.requests),1);self.assertIn(b'RIFF',self.requests[0]);self.assertIn(b'whisper-large-v3-turbo',self.requests[0])
            self.assertEqual(self.transport.session.timeout.connect,10);self.assertEqual(self.transport.session.timeout.total,30)
            self.assertTrue(self.transport.session.connector._ssl);self.assertIsNone(pool.active);self.assertIsNotNone(pool.cached)
            self.assertEqual((CONNECT_SECONDS,ADDRESS_SECONDS,ASR_CONNECT_SECONDS),(3,1,10))
            self.assertEqual(sum(e['event']=='groq_request_dispatch' for e in self.events),1)
        finally:await pool.shutdown();await self.transport.close()
    async def test_cancellation_during_dns_releases_service_and_retires_pool_without_http(self):
        resolver=Resolver(self.port);connector,endpoint=self.setup_provider(resolver)
        pool=RequestPool(factory=lambda emit:GroqASR(emit,transport=self.transport));service=Service(pool.lease,writer=self.emit)
        try:
            with connector,endpoint:
                task=asyncio.create_task(service.call(base64.b64encode(bytes(640)).decode(),2,'asr-dns-cancel'))
                await asyncio.wait_for(resolver.entered.wait(),1);task.cancel()
                with self.assertRaises(asyncio.CancelledError):await task
            self.assertTrue(service.states['asr-dns-cancel']['cleanup_ok']);self.assertFalse(service.lock.locked());self.assertFalse(service.poisoned)
            self.assertIsNone(pool.active);self.assertIsNone(pool.cached);self.assertTrue(self.transport.session.closed);self.assertFalse(self.requests);self.assertEqual(resolver.calls,1)
        finally:await pool.shutdown();await self.transport.close()
    async def test_bounded_dns_failure_is_one_attempt_and_cleans_transport(self):
        resolver=Resolver(self.port);connector,endpoint=self.setup_provider(resolver)
        try:
            with connector,endpoint,patch('me3_assistant.worker_readiness.DNS_SECONDS',.05):
                with self.assertRaises(TimeoutError):await self.transport.request('asr',json={})
            self.assertFalse(self.transport.active);self.assertFalse(self.requests);self.assertEqual(resolver.calls,1)
            self.assertEqual(sum(e['event']=='groq_request_dispatch' for e in self.events),0)
        finally:await self.transport.close()

if __name__=='__main__':unittest.main()
