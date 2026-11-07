"""Distinct user generations after ASR cancellation; no hosted/device calls."""
import asyncio,base64,unittest
from me3_assistant.groq_server import Service,make_server
from me3_assistant.worker_readiness import WorkerReadiness
from me3_core.pool import RequestPool

class ReleaseTests(unittest.IsolatedAsyncioTestCase):
 def setup_service(self):
  self.providers=[];self.rows=[];self.entered=asyncio.Event();self.block=True;self.cleanup_failure=False
  owner=self
  class Provider:
   def __init__(self,emit):self.calls=[];self.closed=False;self.prepares=0;owner.providers.append(self)
   async def prepare_connection(self):
    self.prepares+=1;return dict(http_requests=0,model_requests=0,audio_bytes=0,hostname_TLS_verified=True,connector_reused_for_transcription=True)
   async def transcribe(self,pcm,*args):
    self.calls.append(pcm);owner.entered.set()
    if owner.block:await asyncio.Event().wait()
    return {'text':'new user generation'}
   async def close(self):
    if owner.cleanup_failure:raise RuntimeError('fixture cleanup failure')
    self.closed=True
  pool=RequestPool(Provider);service=Service(pool.lease,writer=lambda e,**k:self.rows.append((e,k)));ready=WorkerReadiness(pool,service)
  return pool,service,ready
 async def test_canceled_generation_releases_then_next_distinct_generation_uses_one_fresh_connection(self):
  pool,service,ready=self.setup_service();await ready.check();pcm=base64.b64encode(bytes(640)).decode()
  async def call(*a):
   method=getattr(ready,'transcribe',None)
   if method:return await method(*a)
   # Exact old registered-server gate: demonstrate the pre-fix failure.
   if not ready.usable():raise ValueError('asr_worker_not_ready')
   return await service.call(*a)
  task=asyncio.create_task(call(pcm,1,'asr-cancel-prefix'));await self.entered.wait();task.cancel()
  with self.assertRaises(asyncio.CancelledError):await task
  self.assertTrue(self.providers[0].closed);self.assertIsNone(pool.cached)
  self.assertEqual(service.states['asr-cancel-prefix']['state'],'released')
  self.block=False
  try:
   result=await call(pcm,2,'asr-new-user');self.assertTrue(result['ok']);self.assertTrue(ready.usable())
   self.assertEqual([len(p.calls) for p in self.providers],[1,1]);self.assertEqual([p.prepares for p in self.providers],[1,0]);self.assertEqual(pool.epoch,2)
   again=await call(pcm,3,'asr-later-user');self.assertTrue(again['ok']);self.assertEqual([len(p.calls) for p in self.providers],[1,2]);self.assertTrue(ready.usable())
  finally:await pool.shutdown()
 async def test_registered_server_entry_uses_release_aware_worker_gate(self):
  pool,_,_=self.setup_service();server=make_server(pool);ready_tool=server._tool_manager.get_tool('network_readiness').fn;transcribe=server._tool_manager.get_tool('transcribe').fn
  self.assertTrue((await ready_tool())['ok']);pcm=base64.b64encode(bytes(640)).decode()
  task=asyncio.create_task(transcribe(pcm,1,'asr-registered-cancel'));await self.entered.wait();task.cancel()
  with self.assertRaises(asyncio.CancelledError):await task
  self.block=False
  try:self.assertTrue((await transcribe(pcm,2,'asr-registered-next'))['ok']);self.assertEqual([len(p.calls) for p in self.providers],[1,1])
  finally:await pool.shutdown()
 async def test_failed_cleanup_remains_closed_to_new_generation(self):
  pool,service,ready=self.setup_service();await ready.check();self.cleanup_failure=True;pcm=base64.b64encode(bytes(640)).decode()
  task=asyncio.create_task(ready.transcribe(pcm,1,'asr-bad-release'));await self.entered.wait();task.cancel()
  with self.assertRaises(asyncio.CancelledError):await task
  self.assertFalse(ready.usable());self.assertTrue(service.poisoned)
  with self.assertRaises(ValueError):await ready.transcribe(pcm,2,'asr-blocked-next')
  self.assertEqual(len(self.providers),1);self.cleanup_failure=False;await pool.shutdown()
 async def test_external_pool_replacement_or_poison_never_gains_readiness(self):
  for mutation in ['epoch','provider','poison']:
   pool,_,ready=self.setup_service();await ready.check();original=pool.cached
   if mutation=='epoch':pool.epoch+=1
   elif mutation=='provider':pool.cached=object()
   else:pool.poisoned=True
   self.assertFalse(ready.usable())
   with self.assertRaises(ValueError):await ready.transcribe(base64.b64encode(bytes(640)).decode(),1,'asr-untrusted')
   pool.cached=original;await pool.shutdown()

if __name__=='__main__':unittest.main()
