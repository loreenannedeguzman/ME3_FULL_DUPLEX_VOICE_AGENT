import asyncio,base64,io,tempfile,unittest,wave
from pathlib import Path
from types import SimpleNamespace
from voice_agent import ROOT,EventLog
from speech_mcp.contracts import identity,decode_pcm,validate_text,pcm_to_wav,wav_to_pcm,normalize_tts_audio
from speech_mcp.controller import TurnController
from speech_mcp.rate_limit import TtsRateLimiter
from speech_mcp.client import SpeechClients,ProviderLimited

class Events:
    def __init__(self):self.rows=[]
    def emit(self,event,**fields):self.rows.append({'event':event,**fields})

class ContractTests(unittest.TestCase):
    def test_invalid_identity_and_text(self):
        for generation,request in [(True,'abc'),(-1,'abc'),(1,'../file'),(10001,'x')]:
            with self.subTest(generation=generation,request=request),self.assertRaises(ValueError):identity(generation,request)
        for text in ['', 'x'*701, 'abc\x00']:
            with self.assertRaises(ValueError):validate_text(text)
    def test_audio_bounds_and_base64(self):
        for value in ['bad*',base64.b64encode(b'x').decode(),base64.b64encode(bytes(32002)).decode()]:
            with self.assertRaises(ValueError):decode_pcm(value,16000,1)
        with self.assertRaises(ValueError):decode_pcm('AAAA',44100,1)
    def test_wav_format_and_truncation(self):
        pcm=bytes(4800);self.assertEqual(wav_to_pcm(pcm_to_wav(pcm,24000)),pcm)
        with self.assertRaises(ValueError):wav_to_pcm(pcm_to_wav(pcm,16000))
        with self.assertRaises(ValueError):wav_to_pcm(pcm_to_wav(pcm,24000)[:-4])

    def test_tts_normalizes_declared_formats_and_rejects_wrong_rate(self):
        pcm=bytes(4800)
        self.assertEqual(normalize_tts_audio(pcm_to_wav(pcm,24000),'audio/wav'),pcm)
        self.assertEqual(normalize_tts_audio(pcm,'audio/L16;rate=24000'),pcm)
        for mime in ['audio/L16;rate=16000','audio/mp3']:
            with self.assertRaises(ValueError):normalize_tts_audio(pcm,mime)

class Provider:
    def __init__(self):self.calls=0
    async def answer(self,history):self.calls+=1;return 'A new answer.'

class FakeClients:
    def __init__(self,delayed_role=None,ignore_cancel=False):
        self.delayed_role=delayed_role;self.ignore_cancel=ignore_cancel;self.started=asyncio.Event();self.release=asyncio.Event()
    async def call(self,role,generation,**kwargs):
        if role==self.delayed_role:
            self.started.set()
            try:await self.release.wait()
            except asyncio.CancelledError:
                if not self.ignore_cancel:raise
        if role=='asr':return {'text':'Please give a new answer.'}
        return {'pcm_b64':base64.b64encode(bytes(960)).decode()}

class TurnCancellationTests(unittest.IsolatedAsyncioTestCase):
    def create(self,clients):
        self.events=Events();self.provider=Provider();self.played=[]
        async def play(pcm,generation):self.played.append(generation)
        return TurnController(clients,self.provider,self.events,play)
    async def test_late_asr_after_cancel_never_reaches_reasoning_or_playback(self):
        clients=FakeClients('asr',True);controller=self.create(clients)
        generation=controller.interrupt();controller.submit(bytes(6400),generation)
        await clients.started.wait();task=controller.task;controller.interrupt()
        await task
        self.assertEqual(self.provider.calls,0);self.assertEqual(self.played,[])
        self.assertTrue(any(x['event']=='stale_generation_rejected' for x in self.events.rows))
        await controller.close()
    async def test_late_tts_after_cancel_never_plays_and_new_turn_recovers(self):
        clients=FakeClients('tts',True);controller=self.create(clients)
        generation=controller.interrupt();controller.submit(bytes(6400),generation)
        await clients.started.wait();task=controller.task;new_generation=controller.interrupt();await task
        self.assertEqual(self.played,[])
        clients.delayed_role=None;controller.submit(bytes(6400),new_generation);await controller.task
        self.assertEqual(self.played,[new_generation]);self.assertEqual(controller.turns,2)
        await controller.close()
    async def test_cancel_pending_tts_is_propagated(self):
        clients=FakeClients('tts');controller=self.create(clients);controller.greet()
        await clients.started.wait();task=controller.task;controller.interrupt()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(self.played,[]);await controller.close()
    async def test_mcp_identity_mismatch_is_rejected(self):
        class BadSession:
            async def call_tool(self,name,arguments,**kwargs):
                return SimpleNamespace(isError=False,structuredContent={'ok':True,'generation':arguments['generation']+1,'request_id':arguments['request_id'],'server_role':'asr'},content=[])
        clients=SpeechClients(Events());clients.sessions['asr']=BadSession()
        with self.assertRaisesRegex(ValueError,'identity'):await clients.call('asr',1,pcm_b64='AAAA')
    async def test_quota_limit_blocks_following_requests_without_another_server_call(self):
        class LimitedSession:
            def __init__(self):self.calls=0
            async def call_tool(self,*args,**kwargs):
                self.calls+=1
                return SimpleNamespace(isError=False,structuredContent={'ok':False,'error':'provider_error','code':429},content=[])
        session=LimitedSession();clients=SpeechClients(Events());clients.sessions['tts']=session
        with self.assertRaises(ProviderLimited):await clients.call('tts',1,text='Test')
        with self.assertRaises(ProviderLimited):await clients.call('tts',2,text='Test again')
        self.assertEqual(session.calls,1);self.assertTrue(clients.limit_reached.is_set())
    async def test_reasoning_quota_stops_without_an_extra_tts_request(self):
        class Limited(Exception):code=429
        class LimitedProvider:
            async def answer(self,history):raise Limited()
        class TrackedClients(FakeClients):
            def __init__(self):super().__init__();self.limit_reached=asyncio.Event();self.roles=[]
            async def call(self,role,*args,**kwargs):
                self.roles.append(role);return await super().call(role,*args,**kwargs)
        clients=TrackedClients();controller=self.create(clients);controller.provider=LimitedProvider()
        generation=controller.interrupt();controller.submit(bytes(6400),generation);await controller.task
        self.assertTrue(clients.limit_reached.is_set());self.assertEqual(clients.roles,['asr']);self.assertEqual(self.played,[])
        await controller.close()
    async def test_turn_budget_prevents_extra_provider_requests(self):
        clients=FakeClients();controller=self.create(clients);controller.max_turns=1
        generation=controller.interrupt();controller.submit(bytes(6400),generation);await controller.task
        generation=controller.interrupt();controller.submit(bytes(6400),generation)
        self.assertEqual(controller.turns,1);self.assertEqual(self.provider.calls,1)
        await controller.close()

class RealMcpCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_sdk_cancel_reaches_server_and_session_stays_usable(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as temp:
            events=EventLog(Path(temp)/'run')
            try:
                async with SpeechClients(events,module='tests.mcp_fixture_server') as clients:
                    task=asyncio.create_task(clients.call('asr',1,pcm_b64='synthetic-only'))
                    await asyncio.sleep(.15);task.cancel()
                    with self.assertRaises(asyncio.CancelledError):await task
                    await asyncio.sleep(.1)
                    result=clients.unpack(await clients.sessions['asr'].call_tool('server_info',{}))
                    self.assertGreaterEqual(result['cancelled_calls'],1)
            finally:events.close()



class RateLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_three_request_window_is_preserved_across_restarts(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as temp:
            now=[0.0];waits=[];events=Events()
            async def sleep(seconds):waits.append(seconds);now[0]+=seconds
            path=Path(temp)/'budget.json'
            limiter=TtsRateLimiter(path,events.emit,clock=lambda:now[0],sleep=sleep)
            for _ in range(3):await limiter.acquire()
            other=TtsRateLimiter(path,events.emit,clock=lambda:now[0],sleep=sleep)
            await other.acquire()
            self.assertEqual(waits,[61.0]);self.assertEqual(now[0],61.0)
    async def test_cancelled_wait_does_not_reserve_a_provider_request(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as temp:
            entered=asyncio.Event();path=Path(temp)/'budget.json'
            async def sleep(seconds):entered.set();await asyncio.Event().wait()
            limiter=TtsRateLimiter(path,Events().emit,clock=lambda:0.0,sleep=sleep)
            for _ in range(3):await limiter.acquire()
            original=path.read_text();task=asyncio.create_task(limiter.acquire())
            await entered.wait();task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
            self.assertEqual(path.read_text(),original)

if __name__=='__main__':unittest.main()
