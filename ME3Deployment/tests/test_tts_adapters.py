import asyncio,json,os,tempfile,unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,AsyncMock
import httpx
from speech_mcp.tts_config import TTSConfig
from speech_mcp.tts_adapters import ElevenTTS,ElevenBudget,FliteTTS,GoogleTTS,validate_pcm,MAX_PCM_BYTES,TTSProviderError,ROOT
from speech_mcp.client import SpeechClients,SpeechServiceFailed,ProviderLimited

class Events:
    def __init__(self,path=None):self.rows=[];self.path=path
    def emit(self,event,**fields):self.rows.append({'event':event,**fields})

class ConfigTests(unittest.TestCase):
    def test_default_preserves_google_and_explicit_choices(self):
        self.assertEqual(TTSConfig.from_env({}).provider,'google')
        for name in ('google','elevenlabs','flite'):
            self.assertEqual(TTSConfig.from_env({'TTS_PROVIDER':name}).provider,name)
        for name in ('','auto','GOOGLE','unknown'):
            with self.assertRaisesRegex(ValueError,'invalid_tts_provider'):TTSConfig.from_env({'TTS_PROVIDER':name})
    def test_invalid_configuration_and_secret_safe_repr(self):
        for setting in ({'TTS_TIMEOUT_SECONDS':'nan'},{'TTS_TIMEOUT_SECONDS':'31'},{'TTS_TIMEOUT_SECONDS':'x'},{'TTS_MODEL':'private-canary'},{'TTS_PROVIDER':'elevenlabs','ELEVENLABS_MODEL':'../bad'},{'TTS_PROVIDER':'elevenlabs','ELEVENLABS_VOICE_ID':'bad/key'},{'TTS_PROVIDER':'flite','FLITE_VOICE':'kal:escape'},{'TTS_PROVIDER':'elevenlabs','ELEVENLABS_REQUEST_BUDGET':'-1'}):
            with self.subTest(setting=setting),self.assertRaises(ValueError):TTSConfig.from_env(setting)
        self.assertNotIn('private-canary',repr(TTSConfig(api_key='private-canary')))
    def test_eleven_readiness_requires_access_and_budget(self):
        c=TTSConfig(provider='elevenlabs',model='eleven_flash_v2_5',voice='')
        for changes,reason in [({},'key'),({'api_key':'fake'},'voice'),({'api_key':'fake','voice':'voice'},'access_confirmation'),({'api_key':'fake','voice':'voice','allowed':True,'credits_confirmed':True},'budget')]:
            with self.assertRaisesRegex(ValueError,reason):replace(c,**changes).require_ready()
    def test_local_readiness_does_not_guess_or_install(self):
        with self.assertRaisesRegex(ValueError,'local_tts_engine_required'):TTSConfig(provider='flite',ffmpeg='not-an-installed-engine').require_ready()
    def test_pcm_validation_bounds(self):
        self.assertEqual(validate_pcm(bytes(2)),bytes(2))
        for value in (b'',b'x',b'RIFFxx',bytes(MAX_PCM_BYTES+2),'text',None):
            with self.assertRaises(ValueError):validate_pcm(value)

class ElevenTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        (ROOT/'.tmp').mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='test-eleven-')
        self.config=TTSConfig(provider='elevenlabs',model='eleven_flash_v2_5',voice='synthetic_voice',api_key='fake-test-only',allowed=True,credits_confirmed=True,budget_id='test',request_budget=10,character_budget=1000)
        self.calls=[];self.clients=[]
    async def asyncTearDown(self):
        for client in self.clients:await client.aclose()
        self.temp.cleanup()
    def adapter(self,handler,config=None):
        config=config or self.config
        async def capture(request):
            self.calls.append(request)
            result=handler(request)
            return await result if hasattr(result,'__await__') else result
        client=httpx.AsyncClient(transport=httpx.MockTransport(capture),follow_redirects=False)
        self.clients.append(client)
        return ElevenTTS(config,client=client,budget=ElevenBudget(config,self.temp.name))
    async def test_exact_pcm_format_request_and_close(self):
        a=self.adapter(lambda r:httpx.Response(200,content=bytes(480),headers={'content-type':'audio/pcm;rate=24000;channels=1'}))
        self.assertEqual(await a.synthesize('Hello','slow'),bytes(480))
        request=self.calls[0];body=json.loads(request.content)
        self.assertEqual(request.url.params['output_format'],'pcm_24000');self.assertEqual(body['voice_settings']['speed'],.8)
        self.assertEqual(request.headers['xi-api-key'],'fake-test-only');self.assertEqual(len(self.calls),1)
        await a.close();self.assertTrue(a.client.is_closed)
    async def test_malformed_audio_never_retried(self):
        for content,mime in [(b'','audio/pcm'),(b'x','audio/pcm'),(b'RIFFxx','audio/pcm'),(bytes(4),'audio/mpeg'),(bytes(4),'audio/pcm;rate=16000'),(bytes(4),'audio/pcm;channels=2')]:
            with self.subTest(mime=mime):
                before=len(self.calls);a=self.adapter(lambda r:httpx.Response(200,content=content,headers={'content-type':mime}))
                with self.assertRaises(ValueError):await a.synthesize('Test','normal')
                self.assertEqual(len(self.calls),before+1)
    async def test_audio_size_cap(self):
        a=self.adapter(lambda r:httpx.Response(200,content=bytes(MAX_PCM_BYTES+2),headers={'content-type':'audio/pcm'}))
        with self.assertRaisesRegex(ValueError,'tts_audio_size_limit'):await a.synthesize('Test','normal')
    async def test_http_errors_do_not_retry_or_expose_body(self):
        for status in (302,401,403,429,500):
            a=self.adapter(lambda r:httpx.Response(status,text='private-canary',headers={'location':'https://invalid.example'}))
            before=len(self.calls)
            with self.assertRaises(TTSProviderError) as caught:await a.synthesize('Test','normal')
            self.assertEqual(caught.exception.code,status);self.assertNotIn('private-canary',str(caught.exception));self.assertEqual(len(self.calls),before+1)
    async def test_transport_error_is_fixed_without_retry(self):
        def fail(request):raise httpx.ConnectError('private-canary',request=request)
        a=self.adapter(fail)
        with self.assertRaisesRegex(TTSProviderError,'tts_transport_error'):await a.synthesize('Test','normal')
        self.assertEqual(len(self.calls),1)
    async def test_timeout_is_bounded_and_reserved_once(self):
        async def blocked(request):await asyncio.sleep(10)
        a=self.adapter(blocked,replace(self.config,timeout=.02))
        with self.assertRaisesRegex(TTSProviderError,'tts_timeout'):await a.synthesize('Test','normal')
        self.assertEqual(len(self.calls),1)
        self.assertEqual(json.loads(a.budget.path.read_text())['requests'],1)
    async def test_cancel_propagates_and_does_not_refund_request(self):
        started=asyncio.Event()
        async def blocked(request):started.set();await asyncio.sleep(10)
        a=self.adapter(blocked);task=asyncio.create_task(a.synthesize('Test','normal'))
        await started.wait();task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(len(self.calls),1);self.assertEqual(json.loads(a.budget.path.read_text())['requests'],1)
    async def test_persistent_budget_blocks_before_network(self):
        config=replace(self.config,request_budget=1)
        a=self.adapter(lambda r:httpx.Response(200,content=bytes(4),headers={'content-type':'audio/pcm'}),config)
        await a.synthesize('Test','normal');b=self.adapter(lambda r:None,config)
        with self.assertRaisesRegex(TTSProviderError,'tts_request_budget_exhausted'):await b.synthesize('Test','normal')
        self.assertEqual(len(self.calls),1)
    async def test_character_limit_and_bad_state_block_before_network(self):
        a=self.adapter(lambda r:None,replace(self.config,character_budget=2))
        with self.assertRaisesRegex(TTSProviderError,'budget_exhausted'):await a.synthesize('Test','normal')
        a.budget.path.write_text('{"requests":true,"characters":0}')
        with self.assertRaisesRegex(ValueError,'invalid_tts_budget_state'):await a.synthesize('X','normal')
        self.assertFalse(self.calls)

class LocalAndGoogleTests(unittest.IsolatedAsyncioTestCase):
    async def test_google_delegation_preserved(self):
        delegate=SimpleNamespace(synthesize=AsyncMock(return_value=bytes(4)),source_mime='audio/L16;rate=24000',close=AsyncMock())
        with patch('speech_mcp.tts_adapters.GoogleSpeech',return_value=delegate):
            a=GoogleTTS(TTSConfig());self.assertEqual(await a.synthesize('Hello','slow'),bytes(4));await a.close()
        delegate.synthesize.assert_awaited_once_with('Hello','slow');delegate.close.assert_awaited_once()
    async def local(self,kind):
        process=SimpleNamespace(returncode=None)
        process.terminate=lambda:setattr(process,'returncode',-15)
        process.kill=lambda:setattr(process,'returncode',-9)
        async def wait():
            if process.returncode is None:process.returncode=1 if kind=='error' else 0
            return process.returncode
        process.wait=wait
        started=asyncio.Event();calls=[];cwd=[]
        async def read(size):
            started.set()
            if kind in {'cancel','timeout'}:await asyncio.sleep(10)
            if not calls:calls.append(1);return bytes(480)
            return b''
        process.stdout=SimpleNamespace(read=read)
        async def launch(*args,**kwargs):
            cwd.append(kwargs['cwd']);self.assertEqual(Path(kwargs['cwd'],'input.txt').read_text(),'Private speech')
            self.assertNotIn('Private speech',repr(args));self.assertIn('pcm_s16le',args);self.assertIn('atempo=0.75',args)
            self.assertEqual(args[args.index('-ar')+1],'24000');self.assertEqual(args[args.index('-ac')+1],'1')
            return process
        config=TTSConfig(provider='flite',model='flite:kal',voice='kal',ffmpeg=__file__,timeout=.02 if kind=='timeout' else 1)
        with patch('speech_mcp.tts_adapters.asyncio.create_subprocess_exec',launch):
            a=FliteTTS(config)
            if kind=='cancel':
                task=asyncio.create_task(a.synthesize('Private speech','slow'));await started.wait();task.cancel()
                with self.assertRaises(asyncio.CancelledError):await task
            elif kind in {'error','timeout'}:
                with self.assertRaises(TTSProviderError):await a.synthesize('Private speech','slow')
            else:self.assertEqual(await a.synthesize('Private speech','slow'),bytes(480))
            await a.close()
        self.assertTrue(cwd);self.assertFalse(Path(cwd[0]).exists());self.assertIsNotNone(process.returncode)
    async def test_local_audio_conversion_and_temp_cleanup(self):await self.local('ok')
    async def test_local_process_error_cleanup(self):await self.local('error')
    async def test_local_timeout_terminates_process(self):await self.local('timeout')
    async def test_local_cancellation_terminates_process(self):await self.local('cancel')

class ClientLatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_error_latches_and_second_request_never_reaches_mcp(self):
        session=SimpleNamespace(call_tool=AsyncMock(return_value=SimpleNamespace(isError=False,structuredContent={'ok':False,'code':401,'error':'provider_error'},content=[])))
        events=Events();client=SpeechClients(events);client.sessions['tts']=session
        with self.assertRaises(ValueError):await client.call('tts',1,text='Test')
        with self.assertRaises(SpeechServiceFailed):await client.call('tts',2,text='Test')
        self.assertEqual(session.call_tool.await_count,1);self.assertTrue(client.failure.is_set());self.assertFalse(client.limit_reached.is_set())
        self.assertEqual(next(x for x in events.rows if x['event']=='speech_service_failed')['code'],401)
    async def test_quota_latch_preserved(self):
        session=SimpleNamespace(call_tool=AsyncMock(return_value=SimpleNamespace(isError=False,structuredContent={'ok':False,'code':429},content=[])))
        client=SpeechClients(Events());client.sessions['tts']=session
        for generation in (1,2):
            with self.assertRaises(ProviderLimited):await client.call('tts',generation,text='Test')
        self.assertEqual(session.call_tool.await_count,1);self.assertTrue(client.limit_reached.is_set())

class ProductionProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def protocol(self,provider):
        (ROOT/'.tmp').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='test-mcp-adapter-') as temp:
            env={'TTS_PROVIDER':provider,'ELEVENLABS_API_KEY':'','ELEVEN_API_KEY':'','GOOGLE_API_KEY':'','ALLOW_PROVIDER_AUDIO':'0'}
            events=Events(Path(temp)/'events.jsonl')
            with patch.dict(os.environ,env):
                async with SpeechClients(events) as client:
                    self.assertEqual(client.pacer is not None,provider=='google')
                    tools=(await client.sessions['tts'].list_tools()).tools
                    tool=next(t for t in tools if t.name=='synthesize')
                    self.assertEqual(set(tool.inputSchema['properties']),{'text','generation','request_id','style'})
                    if provider=='elevenlabs':
                        with self.assertRaises(ValueError):await client.call('tts',7,text='Test')
                        reason=next(x for x in events.rows if x['event']=='mcp_request_error')['validation_reason']
                        self.assertEqual(reason,'elevenlabs_key_required')
                        with self.assertRaises(SpeechServiceFailed):await client.call('tts',8,text='Test')
            self.assertEqual(len([x for x in events.rows if x['event']=='mcp_connected']),2)
    async def test_real_mcp_eleven_missing_key_no_network_clean_shutdown(self):await self.protocol('elevenlabs')
    async def test_real_mcp_google_metadata_retains_pacer_no_synthesis(self):await self.protocol('google')
