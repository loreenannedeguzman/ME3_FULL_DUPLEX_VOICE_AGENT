"""Expired connector DNS/idle socket, local TLS only; no provider or device."""
import asyncio,os,unittest
from unittest.mock import patch
from me3_assistant import groq_backend as g
from tests.test_assistant_worker_readiness import TLSPreparationTests,Resolver
from me3_assistant.worker_readiness import prepare_connection

class ASRExpiryTests(unittest.IsolatedAsyncioTestCase):
 asyncSetUp=TLSPreparationTests.asyncSetUp
 asyncTearDown=TLSPreparationTests.asyncTearDown
 transport=TLSPreparationTests.transport
 def context(self,connector):
  from contextlib import ExitStack
  stack=ExitStack()
  stack.enter_context(patch.object(g,'groq_connector',return_value=connector))
  stack.enter_context(patch.dict(g.ENDPOINTS,{'asr':'https://me3-fixture.invalid/'}))
  stack.enter_context(patch.dict(os.environ,{'NO_PROXY':'me3-fixture.invalid','no_proxy':'me3-fixture.invalid'}))
  return stack
 async def test_expired_DNS_and_closed_socket_resolve_before_next_single_TLS_POST(self):
  resolver=Resolver(self.port);t,connector,events=self.transport(resolver)
  try:
   with self.context(connector),patch('me3_assistant.transport.ASR_CONNECT_SECONDS',3):
    await prepare_connection(t);await asyncio.sleep(.01)
    self.assertEqual((self.posts,resolver.calls,self.connections),(0,1,1))
    cached_at=connector._cached_hosts._timestamps[('me3-fixture.invalid',443)]
    self.assertEqual((await t.request('asr',data='first'))['text'],'local fixture')
    self.assertEqual(connector._cached_hosts._timestamps[('me3-fixture.invalid',443)],cached_at)
    # The peer closes its first connection. Force the same state as an expired
    # cache without waiting five minutes; retain actual configured TTL/bounds.
    connector.clear_dns_cache();resolver.delay=3.2
    self.assertEqual((await t.request('asr',data='next'))['text'],'local fixture')
    self.assertEqual((resolver.calls,self.posts,self.connections),(2,2,2))
    self.assertEqual((connector._cached_hosts._ttl,connector._keepalive_timeout),(300,60))
    self.assertEqual((t.session.timeout.connect,t.session.timeout.total),(3,30))
    self.assertEqual(len([e for e,k in events.rows if e=='groq_request_dispatch']),2)
    ready=[k['seconds'] for e,k in events.rows if e=='groq_asr_prewire_dns_ready']
    self.assertGreater(ready[-1],3)
  finally:await t.close()
 async def test_cancel_or_DNS_deadline_before_admission_sends_no_POST_and_releases_active(self):
  for cancel in (True,False):
   resolver=Resolver(self.port,hold=True);t,connector,events=self.transport(resolver)
   t.admission.admit=lambda *a:(_ for _ in ()).throw(AssertionError('DNS must precede admission'))
   try:
    with self.context(connector),patch('me3_assistant.worker_readiness.DNS_SECONDS',.1):
     task=asyncio.create_task(t.request('asr',data='fixture'));await resolver.entered.wait()
     if cancel:task.cancel()
     with self.assertRaises(asyncio.CancelledError if cancel else TimeoutError):await task
     self.assertFalse(t.active);self.assertEqual((self.posts,resolver.calls),(0,1))
     self.assertFalse(any(e=='groq_request_dispatch' for e,k in events.rows))
     self.assertTrue(any(e=='groq_asr_prewire_dns_failed' and k['wire_attempts']==0 for e,k in events.rows))
   finally:await t.close()
 async def test_stop_after_resolution_prevents_admission_and_HTTP(self):
  resolver=Resolver(self.port);t,connector,events=self.transport(resolver)
  original=resolver.resolve
  async def resolve(*a,**k):
   result=await original(*a,**k);t.stop_requested=asyncio.Event();t.stop_requested.set();return result
  resolver.resolve=resolve
  t.admission.admit=lambda *a:(_ for _ in ()).throw(AssertionError('stopped request cannot admit'))
  try:
   with self.context(connector):
    with self.assertRaises(asyncio.CancelledError):await t.request('asr',data='fixture')
    self.assertEqual(self.posts,0);self.assertFalse(t.active)
  finally:await t.close()
 async def test_one_total_deadline_covers_DNS_plus_HTTP_without_replay(self):
  resolver=Resolver(self.port);resolver.delay=.08;t,connector,events=self.transport(resolver)
  entered=asyncio.Event()
  class HangingHTTP:
   async def __aenter__(inner):entered.set();await asyncio.Future()
   async def __aexit__(inner,*a):pass
  try:
   with self.context(connector),patch.object(g,'ASR_REQUEST_SECONDS',.2):
    t.ensure_session('asr');start=asyncio.get_running_loop().time()
    with patch.object(t.session,'post',return_value=HangingHTTP()) as post:
     with self.assertRaises(TimeoutError):await t.request('asr',data='fixture')
     self.assertTrue(entered.is_set());self.assertEqual(post.call_count,1)
    self.assertLess(asyncio.get_running_loop().time()-start,.6)
    self.assertFalse(t.active)
  finally:await t.close()
