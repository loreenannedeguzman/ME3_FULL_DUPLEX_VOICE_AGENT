import asyncio,base64,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,AsyncMock
from me3_completion.policy import annotation,listener_ack
from me3_completion.playback import SuspendableBuffer,PlaybackGate
from me3_completion.endpoint import LocalEndpoint
from me3_completion.cache import CueCache,CUE
from me3_completion.controller import ConversationController
from me3_completion.client import ConversationClients
from voice_agent import ROOT,EventLog,Settings
from duplex_audio import PlayoutBuffer
class Events:
    def __init__(self):self.rows=[];self.input_frames=0;self.output_frames=0
    def emit(self,event,**fields):self.rows.append({'event':event,**fields})
class PolicyTests(unittest.TestCase):
    def test_rule_is_strict_duration_and_word_count(self):
        self.assertTrue(annotation('Mm-hmm.',.99)['brief_by_rule']);self.assertFalse(annotation('mm hmm',.5)['brief_by_rule']);self.assertFalse(annotation('okay',1)['brief_by_rule'])
    def test_short_command_is_brief_but_never_semantic_ack(self):
        for text in ['stop','confirm','yes','no','cancel','wait']:
            self.assertTrue(annotation(text,.3)['brief_by_rule']);self.assertFalse(listener_ack(text,.3))
    def test_task_pending_prevents_ignoring_ack(self):
        self.assertTrue(listener_ack('okay',.4));self.assertFalse(listener_ack('okay',.4,task_pending=True))
    def test_invalid_duration_rejected(self):
        for value in [-1,float('nan'),float('inf'),'1']:
            with self.assertRaises(ValueError):annotation('ok',value)
class BufferTests(unittest.IsolatedAsyncioTestCase):
    async def test_suspend_preserves_audio_and_cancel_destroys_it(self):
        buffer=SuspendableBuffer(PlayoutBuffer(12));gate=PlaybackGate(buffer)
        buffer.offer(b'abcd',0);gate.suspend();self.assertEqual(buffer.take(4),(bytes(4),0,0,4))
        wait=asyncio.create_task(gate.wait());await asyncio.sleep(0);self.assertFalse(wait.done())
        gate.resume();await wait;self.assertEqual(buffer.take(4)[0],b'abcd')
        buffer.offer(b'old',0);gate.suspend();self.assertEqual(buffer.clear(),(1,3));gate.resume();self.assertEqual(buffer.take(4)[2],0);self.assertEqual(buffer.offer(b'late',0),'stale')
    async def test_paused_output_remains_bounded(self):
        buffer=SuspendableBuffer(PlayoutBuffer(4));buffer.suspend();self.assertEqual(buffer.offer(b'1234',0),'accepted');self.assertEqual(buffer.offer(b'5',0),'full');self.assertEqual(buffer.take(4)[3],4)
class Analyzer:
    def __init__(self):self.running=0;self.peak=0;self.copies=[];self.release=asyncio.Event();self.started=asyncio.Event();self.closed=False
    def set_sample_rate(self,rate):self.rate=rate
    def update_vad_start_secs(self,value):pass
    def clear(self):pass
    def append_audio(self,pcm,speech):self.copies.append(pcm)
    async def analyze_end_of_turn(self):
        self.running+=1;self.peak=max(self.peak,self.running);self.started.set()
        try:await self.release.wait();return SimpleNamespace(name='INCOMPLETE'),SimpleNamespace(probability=.2)
        finally:self.running-=1
    async def cleanup(self):self.closed=True
class EndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_does_not_release_native_ownership_or_overlap_jobs(self):
        fake=Analyzer();e=LocalEndpoint(Events(),fake);first=asyncio.create_task(e.decide(bytes(6400),1));await fake.started.wait();first.cancel()
        with self.assertRaises(asyncio.CancelledError):await first
        second=asyncio.create_task(e.decide(bytes(9600),2));await asyncio.sleep(.01);self.assertEqual(len(fake.copies),1)
        fake.release.set();self.assertFalse(await second);await e.close();self.assertEqual(fake.peak,1);self.assertTrue(fake.closed);self.assertFalse(e.jobs)
    async def test_timeout_keeps_owned_job_until_cleanup(self):
        fake=Analyzer();e=LocalEndpoint(Events(),fake,deadline=.1)
        with self.assertRaises(TimeoutError):await e.decide(bytes(6400),1)
        self.assertEqual(fake.running,1);fake.release.set();await e.close();self.assertFalse(e.jobs)
    async def test_invalid_audio_and_closed_worker_rejected(self):
        fake=Analyzer();e=LocalEndpoint(Events(),fake)
        for pcm in [bytes(3198),bytes(480002),bytearray(6400)]:
            with self.assertRaises(ValueError):await e.decide(pcm,1)
        await e.close()
        with self.assertRaises(RuntimeError):await e.decide(bytes(6400),1)
class FakeTTS:
    metrics={};source_mime='audio/L16;rate=24000';provider_api='synthetic'
    def __init__(self):self.calls=[];self.closed=False;self.output=bytes(9600)
    async def synthesize_identified(self,*args):self.calls.append(args);await asyncio.sleep(0);return self.output
    async def close(self):self.closed=True
class CacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_cache_only_exact_neutral_cue_and_fresh_identity(self):
        backend=FakeTTS();c=CueCache(backend)
        for g in [1,2]:self.assertEqual(len(await c.synthesize_identified(CUE,'normal',g,'req-'+str(g))),9600)
        self.assertEqual(len(backend.calls),1);self.assertTrue(c.metrics['completion_cue_cache_hit'])
        await c.synthesize_identified('Ordinary answer.','normal',3,'req-3');self.assertEqual(len(backend.calls),2)
        await c.close();self.assertTrue(backend.closed);self.assertIsNone(c.audio)
    async def test_malformed_or_long_cue_is_never_cached(self):
        for output in [b'odd',bytes(48000),b'RIFFbad!']:
            backend=FakeTTS();backend.output=output;c=CueCache(backend)
            with self.assertRaises(ValueError):await c.synthesize_identified(CUE,'normal',1,'r')
            self.assertIsNone(c.audio);await c.close()
    async def test_real_mcp_cached_ids_cancellation_and_clean_shutdown(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as tmp:
            events=EventLog(Path(tmp)/'logs')
            try:
                async with ConversationClients(events,module='tests.completion_mcp_fixture') as clients:
                    first=await clients.call('tts',1,text=CUE,style='normal');second=await clients.call('tts',2,text=CUE,style='normal')
                    self.assertNotEqual(first['request_id'],second['request_id']);self.assertEqual(second['generation'],2);self.assertTrue(second['tts_metrics']['completion_cue_cache_hit'])
                    work=asyncio.create_task(clients.call('tts',3,text='sleep',style='normal'));await asyncio.sleep(.1);work.cancel()
                    with self.assertRaises(asyncio.CancelledError):await work
                    self.assertEqual((await clients.call('tts',4,text='Fresh answer',style='normal'))['generation'],4)
                self.assertFalse(clients.sessions['tts'].abandoned)
            finally:events.close()
            self.assertIn('fixture_tts_closed',(Path(tmp)/'logs/tts-server.stderr.log').read_text())
class ControllerTests(unittest.IsolatedAsyncioTestCase):
    async def test_validated_probe_is_consumed_once_without_second_asr(self):
        calls=[];events=Events()
        class Clients:
            async def call(self,role,generation,**kw):
                calls.append(role)
                if role=='asr':raise AssertionError('duplicate ASR')
                return {'pcm_b64':base64.b64encode(bytes(960)).decode()}
        class Provider:
            pending=None
            async def answer(self,history):return 'Four.'
        async def play(*args):pass
        c=ConversationController(Clients(),Provider(),events,play);g=c.interrupt()
        c.submit_recognized(bytes(6400),g,{'text':'What is two plus two?','generation':g,'request_id':'asr-probe'})
        await c.task;self.assertEqual(calls,['tts']);self.assertEqual(c.turns,1);self.assertFalse(c.prefetch.ready)
        c.interrupt();c.submit_recognized(bytes(6400),g,{'text':'stale','generation':g,'request_id':'asr-old'});self.assertEqual(calls,['tts']);await c.close()
    async def test_wrong_identity_is_not_adopted(self):
        c=ConversationController(None,None,Events(),None)
        with self.assertRaises(ValueError):c.submit_recognized(bytes(6400),0,{'text':'x','generation':1,'request_id':'r'})
        await c.close()
