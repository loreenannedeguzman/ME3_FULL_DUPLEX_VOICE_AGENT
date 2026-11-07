import asyncio,base64,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import anyio
from me3_core.pool import RequestPool,SCOPE,ScopedGoogle,dispatch
from me3_core.server import make_server
from me3_m7.server import ASRService
from me3_core.controller import CoreTurnController
from me3_core.client import CoreSpeechClients
from voice_agent import EventLog
ROOT=Path(__file__).resolve().parents[1];PCM=bytes(6400);B64=base64.b64encode(PCM).decode()
class PoolTests(unittest.IsolatedAsyncioTestCase):
    def setup_pool(self,mode='reuse_after_success',close_error=False):
        state={'made':0,'closed':0,'started':asyncio.Event()};events=[]
        class Fake:
            def __init__(inner,emit):state['made']+=1;inner.emit=emit
            async def transcribe(inner,pcm,*args):
                state['started'].set();inner.emit('provider_request_start')
                if pcm[0]==1:await asyncio.Event().wait()
                if pcm[0]==2:raise TimeoutError('synthetic')
                return {'text':'ok'}
            async def close(inner):
                await anyio.sleep(.01);state['closed']+=1
                if close_error:raise RuntimeError('synthetic close')
        pool=RequestPool(Fake,mode);service=ASRService(pool.lease,lambda event,**kw:events.append({'event':event,**kw}));return pool,service,state,events
    async def test_success_reuses_one_client_and_shutdown_closes_once(self):
        pool,service,state,events=self.setup_pool()
        for g in (1,2):self.assertTrue((await service.call(B64,g,'req-'+str(g)))['ok'])
        self.assertEqual((state['made'],state['closed']),(1,0));await pool.shutdown();self.assertEqual(state['closed'],1)
        borrows=[r for r in events if r['event']=='core_pool_borrow'];self.assertEqual([r['reused'] for r in borrows],[False,True]);self.assertEqual([r['request_id'] for r in borrows],['req-1','req-2'])
    async def test_cancel_retires_before_next_explicit_request(self):
        pool,service,state,events=self.setup_pool()
        async with anyio.create_task_group() as group:
            group.start_soon(service.call,base64.b64encode(b'\1'+bytes(6399)).decode(),1,'cancel');await state['started'].wait();group.cancel_scope.cancel()
        self.assertEqual(state['closed'],1);self.assertTrue(service.states['cancel']['cleanup_ok']);self.assertIsNone(pool.cached)
        self.assertTrue((await service.call(B64,2,'next'))['ok']);self.assertEqual(state['made'],2);await pool.shutdown()
    async def test_timeout_retires_without_retry(self):
        pool,service,state,events=self.setup_pool();result=await service.call(base64.b64encode(b'\2'+bytes(6399)).decode(),1,'timeout')
        self.assertFalse(result['ok']);self.assertEqual(state['made'],1);self.assertEqual(state['closed'],1);self.assertIsNone(pool.cached);await pool.shutdown()
    async def test_controlled_deadline_reaps_active_client(self):
        pool,service,state,events=self.setup_pool();result=await service.call(base64.b64encode(b'\1'+bytes(6399)).decode(),1,'deadline',deadline_ms=100)
        self.assertEqual(result['error_type'],'TimeoutError');self.assertTrue(result['cleanup_ok']);self.assertIsNone(pool.active);self.assertIsNone(pool.cached);await pool.shutdown()
    async def test_close_failure_poison_prevents_another_borrow(self):
        pool,service,state,events=self.setup_pool(close_error=True);result=await service.call(base64.b64encode(b'\2'+bytes(6399)).decode(),1,'bad-close')
        self.assertFalse(result['cleanup_ok']);self.assertTrue(pool.poisoned)
        with self.assertRaises(ValueError):pool.lease(lambda *a,**k:None)
        await pool.shutdown()
    async def test_per_request_is_explicit_and_no_silent_fallback(self):
        pool,service,state,events=self.setup_pool('per_request')
        for g in (1,2):await service.call(B64,g,'per-'+str(g))
        self.assertEqual((state['made'],state['closed']),(2,2));await pool.shutdown()
        with self.assertRaises(ValueError):RequestPool(mode='automatic')
    async def test_one_lease_one_request_and_closed_pool_cannot_restart(self):
        pool,_,_,_=self.setup_pool();lease=pool.lease(lambda *a,**k:None)
        with self.assertRaises(ValueError):pool.lease(lambda *a,**k:None)
        await lease.transcribe(PCM,'low',None)
        with self.assertRaises(ValueError):await lease.transcribe(PCM,'low',None)
        await lease.close();await lease.close();await pool.shutdown()
        with self.assertRaises(ValueError):pool.lease(lambda *a,**k:None)
    async def test_real_mcp_cancel_replacement_and_idle_shutdown(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as temp:
            events=EventLog(Path(temp)/'events')
            try:
                async with CoreSpeechClients(events,module='tests.core_mcp_fixture') as client:
                    await client.call('asr',1,pcm_b64=B64)
                    task=asyncio.create_task(client.call('asr',2,pcm_b64=base64.b64encode(b'\1'+bytes(6399)).decode()));await asyncio.sleep(.2);task.cancel()
                    with self.assertRaises(asyncio.CancelledError):await task
                    self.assertEqual((await client.call('asr',3,pcm_b64=B64))['text'],'synthetic replacement')
            finally:events.close()
            data=[json.loads(x) for x in events.path.read_text().splitlines()];self.assertTrue(any(r['event']=='m7_release_barrier_complete' for r in data))
            stderr=(events.path.parent/'asr-server.stderr.log').read_text();self.assertIn('core_pool_shutdown',stderr)
class ControllerTests(unittest.IsolatedAsyncioTestCase):
    def setup_controller(self,mode='empty'):
        self.rows=[];self.spoken=[];self.reasons=[]
        class Events:
            def emit(inner,event,**kw):self.rows.append({'event':event,**kw})
        class Clients:
            async def call(inner,role,g,**kw):
                if role=='tts':self.spoken.append(g);return {'pcm_b64':base64.b64encode(bytes(960)).decode()}
                if mode=='empty':return {'text':''}
                return {'text':'What is two plus two?'}
        class Reasoner:
            pending={'unsafe':'unconfirmed'}
            async def answer(inner,history):self.reasons.append(history);await asyncio.Event().wait()
        async def play(*_):pass
        return CoreTurnController(Clients(),Reasoner(),Events(),play,turn_deadline_seconds=1)
    async def test_empty_speech_prompts_once_and_settles_without_reasoning(self):
        c=self.setup_controller();c.submit(PCM,c.interrupt());await c.task;self.assertEqual(self.spoken,[1]);self.assertFalse(self.reasons);self.assertIsNone(c.provider.pending);self.assertEqual(sum(r['event']=='core_turn_settled' for r in self.rows),1);await c.close()
    async def test_real_outstanding_turn_deadline_cancels_and_settles(self):
        c=self.setup_controller('blocked');c.submit(PCM,c.interrupt());await c.task;self.assertTrue(any(r['event']=='core_turn_deadline' for r in self.rows));self.assertEqual(self.spoken,[1]);self.assertIsNone(c.provider.pending);await c.close()
    async def test_stale_speech_never_spends_tts_or_plays(self):
        c=self.setup_controller();c.interrupt();self.assertFalse(await c.speak('obsolete',0,'normal'));self.assertEqual(self.spoken,[]);await c.close()
    async def test_canceled_turn_settles_old_generation_only(self):
        c=self.setup_controller('blocked');c.submit(PCM,c.interrupt());await asyncio.sleep(.02);old=c.task;c.interrupt()
        with self.assertRaises(asyncio.CancelledError):await old
        self.assertTrue(any(r['event']=='core_turn_settled' and r['generation']==1 and r['outcome']=='canceled' for r in self.rows));self.assertEqual(self.spoken,[]);await c.close()
