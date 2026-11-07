"""Owned ASR transport against local TLS; no provider/audio/device work."""
import asyncio,os,ssl,unittest
from types import SimpleNamespace
from unittest.mock import patch
import aiohttp
from me3_assistant import groq_backend as g
from me3_assistant.transport import bind_connection_deadline
from tests.test_assistant_worker_readiness import TLSPreparationTests,Resolver

class ASRReconnectTests(unittest.IsolatedAsyncioTestCase):
 asyncSetUp=TLSPreparationTests.asyncSetUp
 asyncTearDown=TLSPreparationTests.asyncTearDown
 transport=TLSPreparationTests.transport
 async def test_idle_expiry_reconnect_accepts_TLS_longer_than_one_second_once(self):
  resolver=Resolver(self.port);t,c,e=self.transport(resolver);c._keepalive_timeout=.01
  original=c._wrap_create_connection;slow=False
  async def delayed(*args,**kwargs):
   if slow:await asyncio.sleep(1.25)
   return await original(*args,**kwargs)
  c._wrap_create_connection=delayed
  try:
   with patch.object(g,'groq_connector',return_value=c),patch.dict(g.ENDPOINTS,{'asr':'https://me3-fixture.invalid/'}),patch.dict(os.environ,{'NO_PROXY':'me3-fixture.invalid','no_proxy':'me3-fixture.invalid'}):
    self.assertEqual((await t.request('asr',data='first'))['text'],'local fixture')
    await asyncio.sleep(.02);slow=True
    self.assertEqual((await t.request('asr',data='second'))['text'],'local fixture')
    self.assertEqual((self.posts,self.connections,resolver.calls),(2,2,1))
    self.assertEqual((c._me3_normal_connect_seconds,c._me3_normal_address_seconds),(10,4))
    self.assertEqual((t.session.timeout.connect,t.session.timeout.total),(10,30))
    self.assertEqual(sum(n=='groq_request_dispatch' for n,_ in e.rows),2)
    self.assertFalse(any(n=='normal_address_connect_timeout' for n,_ in e.rows));self.assertFalse(c._acquired)
  finally:await t.close()
 async def test_cancel_slow_reconnect_sends_no_HTTP_and_never_retries(self):
  t,c,e=self.transport(Resolver(self.port));entered=asyncio.Event();calls=0
  async def stalled(*args,**kwargs):
   nonlocal calls
   calls+=1;entered.set();await asyncio.Future()
  c._wrap_create_connection=stalled
  try:
   with patch.object(g,'groq_connector',return_value=c),patch.dict(g.ENDPOINTS,{'asr':'https://me3-fixture.invalid/'}):
    task=asyncio.create_task(t.request('asr',data='fixture'));await entered.wait();task.cancel()
    with self.assertRaises(asyncio.CancelledError):await task
    self.assertEqual((calls,self.posts),(1,0));self.assertFalse(c._acquired);self.assertFalse(t.active)
    self.assertFalse(any(n=='normal_address_connect_timeout' for n,_ in e.rows))
  finally:await t.close()
 async def test_stalled_address_stays_bounded_and_dispatch_is_not_replayed(self):
  t,c,e=self.transport(Resolver(self.port));calls=0
  async def stalled(*args,**kwargs):
   nonlocal calls
   calls+=1;await asyncio.Future()
  c._wrap_create_connection=stalled
  try:
   with patch.object(g,'groq_connector',return_value=c),patch.dict(g.ENDPOINTS,{'asr':'https://me3-fixture.invalid/'}),patch('me3_assistant.transport.ASR_CONNECT_SECONDS',.2),patch('me3_assistant.transport.ASR_ADDRESS_SECONDS',.05):
    with self.assertRaises(TimeoutError):await t.request('asr',data='fixture')
    self.assertEqual((calls,self.posts),(1,0));self.assertFalse(c._acquired);self.assertFalse(t.active)
    self.assertEqual(sum(n=='groq_request_dispatch' for n,_ in e.rows),1)
    self.assertTrue(any(n=='normal_connection_addresses_exhausted' for n,_ in e.rows))
  finally:await t.close()
 async def test_ASR_longer_budget_does_not_accept_untrusted_certificate(self):
  t,c,e=self.transport(Resolver(self.port),ssl.create_default_context())
  try:
   with patch.object(g,'groq_connector',return_value=c),patch.dict(g.ENDPOINTS,{'asr':'https://me3-fixture.invalid/'}):
    with self.assertRaises(aiohttp.ClientConnectorCertificateError):await t.request('asr',data='fixture')
    self.assertEqual(self.posts,0);self.assertFalse(c._acquired)
  finally:await t.close()
 async def test_address_budget_validation_and_binding_are_immutable(self):
  async def original():return None
  p=SimpleNamespace(owned_session=SimpleNamespace(connector=SimpleNamespace(_create_connection=original)),emit=lambda *a,**k:None)
  bind_connection_deadline(p,10,address_seconds=4);bind_connection_deadline(p,10,address_seconds=4)
  with self.assertRaisesRegex(ValueError,'already_bound'):bind_connection_deadline(p,10,address_seconds=3)
  for value in (0,-1,11,True,float('nan')):
   with self.assertRaises(ValueError):bind_connection_deadline(p,10,address_seconds=value)

if __name__=='__main__':unittest.main()
