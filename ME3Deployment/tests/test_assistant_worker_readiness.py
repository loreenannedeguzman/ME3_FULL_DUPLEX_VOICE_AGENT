"""No provider/device calls: actual-worker warm TLS, cache ownership and lifecycle."""
import asyncio,base64,json,os,socket,ssl,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,patch
import aiohttp
from me3_assistant import groq_backend as g
from me3_assistant.groq_server import Service
from me3_assistant.worker_readiness import WorkerReadiness,prepare_connection,prepare_speech_worker
from me3_core.pool import RequestPool
from tests.test_assistant_address_fallback import CERTIFICATE,TEST_PRIVATE_KEY
from tests.test_assistant_groq_adapter import Events

class Resolver(aiohttp.abc.AbstractResolver):
 def __init__(self,port,hold=False):self.port=port;self.calls=0;self.hold=hold;self.delay=0;self.entered=asyncio.Event()
 async def resolve(self,host,port=0,family=socket.AF_INET):
  self.calls+=1;self.entered.set()
  if self.hold:await asyncio.Future()
  if self.delay:await asyncio.sleep(self.delay)
  return [dict(hostname=host,host='127.0.0.1',port=self.port,family=socket.AF_INET,proto=socket.IPPROTO_TCP,flags=socket.AI_NUMERICHOST)]
 async def close(self):pass

class TLSPreparationTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.temp=tempfile.TemporaryDirectory();p=Path(self.temp.name);(p/'cert.pem').write_text(CERTIFICATE);(p/'key.pem').write_text(TEST_PRIVATE_KEY)
  self.server_ssl=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);self.server_ssl.load_cert_chain(p/'cert.pem',p/'key.pem')
  self.client_ssl=ssl.create_default_context(cadata=CERTIFICATE)
  self.handlers=set();self.posts=0;self.connections=0
  async def peer(reader,writer):
   task=asyncio.current_task();self.handlers.add(task);self.connections+=1
   try:
    head=await reader.readuntil(b'\r\n\r\n')
    length=next((int(line.split(b':',1)[1]) for line in head.split(b'\r\n') if line.lower().startswith(b'content-length:')),0)
    await reader.readexactly(length);self.posts+=1
    body=b'{"text":"local fixture"}';writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: '+str(len(body)).encode()+b'\r\nConnection: close\r\n\r\n'+body);await writer.drain()
   except (asyncio.IncompleteReadError,ConnectionError):pass
   finally:
    writer.close()
    try:await writer.wait_closed()
    except (ConnectionError,ssl.SSLError):pass
    self.handlers.discard(task)
  self.server=await asyncio.start_server(peer,'127.0.0.1',0,ssl=self.server_ssl);self.port=self.server.sockets[0].getsockname()[1]
 async def asyncTearDown(self):
  self.server.close();await self.server.wait_closed()
  if self.handlers:await asyncio.gather(*tuple(self.handlers),return_exceptions=True)
  self.temp.cleanup()
 def transport(self,resolver,context=None):
  connector=aiohttp.TCPConnector(family=socket.AF_INET,ssl=context or self.client_ssl,resolver=resolver,ttl_dns_cache=300,keepalive_timeout=60,limit=1)
  events=Events();t=g.Transport(events.emit,key='fixture',opt_in=True,admission=g.Admission())
  return t,connector,events
 async def test_prepares_zero_HTTP_then_same_session_sends_one_post_without_second_DNS_or_TLS(self):
  resolver=Resolver(self.port);t,connector,events=self.transport(resolver)
  try:
   with patch.object(g,'groq_connector',return_value=connector),patch.dict(g.ENDPOINTS,{'asr':'https://me3-fixture.invalid/'}),patch.dict(os.environ,{'NO_PROXY':'me3-fixture.invalid','no_proxy':'me3-fixture.invalid'}):
    result=await prepare_connection(t);await asyncio.sleep(.01)
    self.assertTrue(result['hostname_TLS_verified']);self.assertEqual((self.posts,resolver.calls,self.connections),(0,1,1))
    self.assertEqual((t.session.timeout.connect,t.session.timeout.total),(10,30));self.assertEqual((result['dns_cache_seconds'],result['keepalive_seconds']),(300,60))
    self.assertFalse(connector._acquired)
    value=await t.request('asr',data='fixture')
    self.assertEqual(value['text'],'local fixture');self.assertEqual((self.posts,resolver.calls,self.connections),(1,1,1))
    self.assertTrue(any(n=='groq_network_connection_reused' for n,_ in events.rows))
  finally:await t.close()
 async def test_cold_DNS_longer_than_request_connect_limit_prepares_before_one_post(self):
  resolver=Resolver(self.port);resolver.delay=3.2;t,connector,_=self.transport(resolver)
  try:
   with patch.object(g,'groq_connector',return_value=connector),patch.dict(g.ENDPOINTS,{'asr':'https://me3-fixture.invalid/'}),patch.dict(os.environ,{'NO_PROXY':'me3-fixture.invalid','no_proxy':'me3-fixture.invalid'}),patch('me3_assistant.transport.ASR_CONNECT_SECONDS',3):
    result=await prepare_connection(t);self.assertGreater(result['dns_seconds'],3);self.assertEqual(self.posts,0)
    self.assertEqual((await t.request('asr',data='fixture'))['text'],'local fixture')
    self.assertEqual((resolver.calls,self.posts,self.connections),(1,1,1))
  finally:await t.close()
 async def test_untrusted_certificate_never_prepares_or_sends_HTTP(self):
  resolver=Resolver(self.port);t,connector,_=self.transport(resolver,ssl.create_default_context())
  try:
   with patch.object(g,'groq_connector',return_value=connector),patch.dict(g.ENDPOINTS,{'asr':'https://me3-fixture.invalid/'}),patch.dict(os.environ,{'NO_PROXY':'me3-fixture.invalid','no_proxy':'me3-fixture.invalid'}):
    with self.assertRaises(aiohttp.ClientError):await prepare_connection(t)
    self.assertEqual(self.posts,0);self.assertFalse(connector._acquired)
  finally:await t.close()
 async def test_DNS_cancel_and_deadline_never_dispatch_HTTP_or_admit(self):
  for cancel in (True,False):
   resolver=Resolver(self.port,True);t,connector,_=self.transport(resolver);t.admission.admit=lambda *a:(_ for _ in ()).throw(AssertionError('No admission in readiness'))
   try:
    with patch.object(g,'groq_connector',return_value=connector),patch.dict(g.ENDPOINTS,{'asr':'https://me3-fixture.invalid/'}),patch('me3_assistant.worker_readiness.DNS_SECONDS',.04):
     task=asyncio.create_task(prepare_connection(t));await resolver.entered.wait()
     if cancel:task.cancel()
     with self.assertRaises(asyncio.CancelledError if cancel else TimeoutError):await task
     self.assertEqual((self.posts,resolver.calls),(0,1));self.assertFalse(t.active);self.assertFalse(connector._acquired)
   finally:await t.close()

class PoolReadinessTests(unittest.IsolatedAsyncioTestCase):
 def setup_pool(self,fail=False):
  self.providers=[];self.rows=[]
  class Provider:
   def __init__(inner,emit):inner.emit=emit;inner.closed=False;inner.prepares=0;inner.calls=0;self.providers.append(inner)
   async def prepare_connection(inner):
    inner.prepares+=1
    if fail:raise TimeoutError()
    return dict(http_requests=0,model_requests=0,audio_bytes=0,hostname_TLS_verified=True,connector_reused_for_transcription=True)
   async def transcribe(inner,*a):inner.calls+=1;return {'text':'fixture answer'}
   async def close(inner):inner.closed=True
  pool=RequestPool(Provider);service=Service(pool.lease,writer=lambda e,**k:self.rows.append((e,k)));return pool,service,WorkerReadiness(pool,service)
 async def test_prepared_actual_provider_is_borrowed_by_ASR_once_and_cleanly_retired_on_shutdown(self):
  pool,service,ready=self.setup_pool()
  self.assertTrue((await ready.check())['ok']);provider=pool.cached;self.assertTrue(ready.usable());self.assertEqual(pool.uses,0)
  out=await service.call(base64.b64encode(bytes(640)).decode(),1,'ready-fixture')
  self.assertTrue(out['ok']);self.assertTrue(out['cleanup_ok']);self.assertIs(pool.cached,provider)
  self.assertEqual((len(self.providers),provider.prepares,provider.calls,pool.uses),(1,1,1,1))
  self.assertFalse((await ready.check())['ok']);self.assertEqual(provider.prepares,1)
  await pool.shutdown();self.assertFalse(ready.usable());self.assertTrue(provider.closed)
 async def test_failed_readiness_is_not_retried_or_retained_and_has_no_ASR_state(self):
  pool,service,ready=self.setup_pool(True)
  self.assertFalse((await ready.check())['ok']);self.assertFalse(ready.usable());self.assertIsNone(pool.cached)
  self.assertEqual(service.states,{});self.assertTrue(self.providers[0].closed);self.assertFalse(service.lock.locked())
  self.assertFalse((await ready.check())['ok']);self.assertEqual(len(self.providers),1);await pool.shutdown()
 async def test_client_requires_same_process_and_verified_zero_model_or_audio_work(self):
  for change in ({},{'server_pid':999},{'http_requests':1},{'hostname_TLS_verified':False}):
   result=dict(ok=True,network_ready=True,server_pid=7,server_role='asr',provider='groq',hostname_TLS_verified=True,connector_reused_for_transcription=True,http_requests=0,model_requests=0,audio_bytes=0,connection_epoch=1,dns_seconds=.1,connection_seconds=.1,dns_cache_seconds=300,keepalive_seconds=60)
   result.update(change)
   class Session:
    async def call_tool(inner,name,*a,**k):return NS(isError=False,structuredContent={'server_pid':7} if name=='server_info' else result)
   e=Events();speech=NS(sessions={'asr':Session()},events=e)
   if change:
    with self.assertRaises(ValueError):await prepare_speech_worker(speech)
   else:self.assertEqual((await prepare_speech_worker(speech))['server_pid'],7)

