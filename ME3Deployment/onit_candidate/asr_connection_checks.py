"""Local TLS/MCP regression checks; no provider, model, audio device or live user."""
import asyncio,base64,json,os,socket,ssl,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import aiohttp
from onit_candidate import asr_server as c
from me3_assistant import groq_backend as g
from me3_assistant.worker_readiness import prepare_connection,WorkerReadiness
from me3_assistant.groq_server import Service
from me3_core.pool import RequestPool
from tests.test_assistant_worker_readiness import Resolver
from tests.test_assistant_address_fallback import CERTIFICATE,TEST_PRIVATE_KEY
from tests.test_assistant_groq_adapter import Events

class ConnectionChecks(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();p=Path(self.temp.name);(p/'cert.pem').write_text(CERTIFICATE);(p/'key.pem').write_text(TEST_PRIVATE_KEY)
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(p/'cert.pem',p/'key.pem');self.client_ssl=ssl.create_default_context(cadata=CERTIFICATE)
        self.handlers=set();self.posts=0;self.connections=0;self.mode='success';self.status=200
        async def peer(reader,writer):
            task=asyncio.current_task();self.handlers.add(task);self.connections+=1;number=self.connections
            try:
                head=await reader.readuntil(b'\r\n\r\n');self.posts+=1
                if self.mode=='drop_all' or self.mode=='drop_probe_socket' and number==1:
                    writer.transport.abort();return
                size=next((int(line.split(b':',1)[1]) for line in head.split(b'\r\n') if line.lower().startswith(b'content-length:')),0)
                if size:await reader.readexactly(size)
                body=b'{"text":"local fixture","language":"english"}'
                writer.write(b'HTTP/1.1 '+str(self.status).encode()+b' Fixture\r\nContent-Length: '+str(len(body)).encode()+b'\r\nConnection: close\r\n\r\n'+body);await writer.drain()
            except (asyncio.IncompleteReadError,ConnectionError):pass
            finally:
                writer.close()
                try:await writer.wait_closed()
                except (ConnectionError,ssl.SSLError):pass
                self.handlers.discard(task)
        self.server=await asyncio.start_server(peer,'127.0.0.1',0,ssl=context);self.port=self.server.sockets[0].getsockname()[1]
    async def asyncTearDown(self):
        self.server.close();await self.server.wait_closed()
        for task in tuple(self.handlers):task.cancel()
        if self.handlers:await asyncio.gather(*tuple(self.handlers),return_exceptions=True)
        self.temp.cleanup()
    def objects(self,*,legacy=False,untrusted=False,hold=False):
        resolver=Resolver(self.port,hold=hold)
        connector=aiohttp.TCPConnector(family=socket.AF_INET,ssl=ssl.create_default_context() if untrusted else self.client_ssl,resolver=resolver,ttl_dns_cache=300,limit=1,**({'keepalive_timeout':60} if legacy else {'force_close':True,'keepalive_timeout':None}))
        events=Events();transport=(g.Transport if legacy else c.FreshTransport)(events.emit,key='LOCAL_FIXTURE_ONLY',opt_in=True,admission=g.Admission())
        return resolver,connector,events,transport
    def patches(self,connector,legacy=False):
        from contextlib import ExitStack
        stack=ExitStack();stack.enter_context(patch.object(g,'groq_connector',return_value=connector) if legacy else patch.object(c.aiohttp,'TCPConnector',return_value=connector))
        stack.enter_context(patch.dict(g.ENDPOINTS,{'asr':'https://me3-fixture.invalid/'}));stack.enter_context(patch.dict(os.environ,{'NO_PROXY':'me3-fixture.invalid','no_proxy':'me3-fixture.invalid'}));return stack
    async def test_reproduces_failed_upload_on_readiness_socket_without_retry(self):
        resolver,connector,events,t=self.objects(legacy=True);self.mode='drop_probe_socket'
        try:
            with self.patches(connector,legacy=True):
                await prepare_connection(t)
                with self.assertRaises(aiohttp.ClientError):await t.request('asr',data=b'local fixture')
            self.assertEqual((self.posts,self.connections),(1,1));self.assertTrue(any(e=='groq_network_connection_reused' for e,_ in events.rows))
            self.assertEqual(sum(e=='groq_request_dispatch' for e,_ in events.rows),1)
        finally:await t.close()
    async def test_fresh_request_avoids_the_same_probe_socket_failure(self):
        resolver,connector,events,t=self.objects();self.mode='drop_probe_socket'
        try:
            with self.patches(connector):
                result=await c.FreshASR(events.emit,transport=t).prepare_connection();await asyncio.sleep(.02)
                self.assertTrue(result['hostname_TLS_verified']);self.assertFalse(result['readiness_socket_reused']);self.assertIsNone(result['keepalive_seconds'])
                self.assertEqual(self.posts,0);self.assertFalse(connector._conns);self.assertFalse(connector._acquired)
                self.assertEqual((await t.request('asr',data=b'local fixture'))['text'],'local fixture')
            self.assertEqual((self.posts,self.connections,resolver.calls),(1,2,1));self.assertFalse(any(e=='groq_network_connection_reused' for e,_ in events.rows))
            self.assertEqual((t.session.timeout.connect,t.session.timeout.total),(10,30))
        finally:await t.close()
    async def test_distinct_turns_reuse_dns_session_but_no_idle_socket(self):
        resolver,connector,events,t=self.objects()
        try:
            with self.patches(connector):
                await c.FreshASR(events.emit,transport=t).prepare_connection();session=t.session
                for payload in (b'first user request',b'new user follow-up'):
                    self.assertEqual((await t.request('asr',data=payload))['text'],'local fixture');self.assertIs(t.session,session)
            self.assertEqual((self.posts,self.connections,resolver.calls),(2,3,1));self.assertFalse(connector._conns)
        finally:await t.close()
    async def test_actual_student_service_preserves_readiness_ownership_for_two_distinct_transcriptions(self):
        resolver,connector,events,t=self.objects();pool=RequestPool(factory=lambda emit:c.FreshASR(emit,transport=t),mode='reuse_after_success');service=Service(pool.lease);ready=WorkerReadiness(pool,service)
        try:
            with self.patches(connector):
                self.assertTrue((await ready.check())['ok']);self.assertEqual(self.posts,0)
                for generation in (1,2):
                    result=await ready.transcribe(base64.b64encode(bytes(6400)).decode(),generation,'fixture-'+str(generation))
                    self.assertTrue(result['ok']);self.assertEqual(result['text'],'local fixture');self.assertTrue(ready.usable())
            self.assertEqual((self.posts,self.connections,resolver.calls),(2,3,1));self.assertIsNone(pool.active)
        finally:await pool.shutdown()
    async def test_failure_after_headers_is_never_replayed_and_logs_only_safe_errno(self):
        resolver,connector,events,t=self.objects();self.mode='drop_all'
        try:
            with self.patches(connector):
                await c.FreshASR(events.emit,transport=t).prepare_connection()
                with self.assertRaises(aiohttp.ClientError):await t.request('asr',data=b'PRIVATE_AUDIO_FIXTURE')
            self.assertEqual(self.posts,1);self.assertEqual(sum(e=='groq_request_dispatch' for e,_ in events.rows),1)
            metadata=[fields for e,fields in events.rows if e=='candidate_asr_socket_error'];self.assertEqual(len(metadata),1)
            self.assertFalse(metadata[0]['exception_text_retained']);self.assertFalse(metadata[0]['automatic_retry'])
            self.assertNotIn('PRIVATE_AUDIO_FIXTURE',str(events.rows));self.assertNotIn('LOCAL_FIXTURE_ONLY',str(events.rows));self.assertFalse(t.active)
        finally:await t.close()
    async def test_http_429_preserves_provider_failure_and_dispatches_once(self):
        resolver,connector,events,t=self.objects();self.status=429
        try:
            with self.patches(connector):
                await c.FreshASR(events.emit,transport=t).prepare_connection()
                with self.assertRaises(g.ProviderError) as caught:await t.request('asr',data=b'local fixture')
            self.assertEqual(caught.exception.code,429);self.assertEqual(self.posts,1)
            self.assertEqual(sum(e=='groq_request_dispatch' for e,_ in events.rows),1)
        finally:await t.close()
    async def test_untrusted_certificate_never_prepares_or_uploads(self):
        resolver,connector,events,t=self.objects(untrusted=True)
        try:
            with self.patches(connector):
                with self.assertRaises(aiohttp.ClientError):await c.FreshASR(events.emit,transport=t).prepare_connection()
            self.assertEqual(self.posts,0);self.assertFalse(connector._acquired)
        finally:await t.close()
    async def test_cancelled_dns_never_uploads_or_admits(self):
        resolver,connector,events,t=self.objects(hold=True)
        t.admission.admit=lambda *a:(_ for _ in ()).throw(AssertionError('Readiness cannot admit'))
        try:
            with self.patches(connector):
                task=asyncio.create_task(c.FreshASR(events.emit,transport=t).prepare_connection());await resolver.entered.wait();task.cancel()
                with self.assertRaises(asyncio.CancelledError):await task
            self.assertEqual(self.posts,0);self.assertFalse(connector._acquired)
        finally:await t.close()
    async def test_transport_is_asr_only_and_keeps_real_public_connector_policy(self):
        events=Events();t=c.FreshTransport(events.emit,key='fixture',opt_in=True,admission=g.Admission())
        try:
            with self.assertRaisesRegex(ValueError,'asr_transport_role'):t.ensure_session('reasoning')
            session=t.ensure_session('asr');self.assertTrue(session.connector.force_close);self.assertEqual(session.connector.family,socket.AF_INET)
            self.assertEqual(session.connector.limit,1);self.assertTrue(session.trust_env)
            self.assertEqual(session.connector._me3_normal_address_seconds,4)
        finally:await t.close()

class MCPWiringChecks(unittest.IsolatedAsyncioTestCase):
    async def test_candidate_uses_existing_student_schema_and_piper_entrypoint(self):
        import ast
        from onit_candidate.voice import SPEECH_MODULE
        from me3_assistant.groq_server import make_server as baseline
        self.assertEqual(SPEECH_MODULE,'onit_candidate.asr_server')
        actual={t.name:t.inputSchema for t in await c.make_server().list_tools()}
        old={t.name:t.inputSchema for t in await baseline().list_tools()}
        self.assertEqual(actual,old)
        tree=ast.parse(Path(c.__file__).read_text());self.assertTrue(any(isinstance(n,ast.ImportFrom) and n.module=='speech_mcp.server' for n in ast.walk(tree)))

if __name__=='__main__':unittest.main(verbosity=2)
