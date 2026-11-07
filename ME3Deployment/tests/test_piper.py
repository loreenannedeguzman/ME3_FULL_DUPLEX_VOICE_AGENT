import asyncio,json,os,sys,math,struct,tempfile,unittest
from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch
from speech_mcp.tts_config import TTSConfig
from speech_mcp.piper_adapter import PCMConverter,PiperTTS
from speech_mcp.tts_adapters import TTSProviderError,make_tts_adapter

class ConversionTests(unittest.TestCase):
    def test_rate_conversion_preserves_duration_and_chunk_continuity(self):
        for rate in (16000,22050,24000,44100,48000):
            raw=b''.join(struct.pack('<h',int(10000*math.sin(2*math.pi*440*i/rate))) for i in range(rate))
            header={'sample_rate':rate,'channels':1,'sample_width':2}
            one=PCMConverter(header);expected=one.feed(raw);one.finish()
            pieces=PCMConverter(header);actual=b''.join(pieces.feed(raw[i:i+513]) for i in range(0,len(raw),513));pieces.finish()
            self.assertEqual(actual,expected);self.assertLessEqual(abs(len(actual)/48000-1),1/12000)
            self.assertLessEqual(max(abs(v[0]) for v in struct.iter_unpack('<h',actual)),10000)
    def test_malformed_header_is_rejected(self):
        for header in ({},[],{'sample_rate':True,'channels':1,'sample_width':2},{'sample_rate':8000,'channels':1,'sample_width':2},{'sample_rate':22050,'channels':2,'sample_width':2},{'sample_rate':22050,'channels':1,'sample_width':4},{'sample_rate':22050,'channels':1,'sample_width':2,'extra':0}):
            with self.subTest(header=header),self.assertRaisesRegex(ValueError,'invalid_piper_header'):PCMConverter(header)
    def test_empty_truncated_and_excess_duration_fail(self):
        for raw in (b'',b'x'):
            c=PCMConverter({'sample_rate':22050,'channels':1,'sample_width':2});c.feed(raw)
            with self.assertRaisesRegex(ValueError,'invalid_piper_pcm'):c.finish()
        c=PCMConverter({'sample_rate':16000,'channels':1,'sample_width':2})
        with self.assertRaisesRegex(ValueError,'tts_audio_size_limit'):c.feed(bytes(16000*2*45+2))
    def test_explicit_provider_validation_and_original_default(self):
        c=TTSConfig.from_env({'TTS_PROVIDER':'piper'})
        self.assertEqual(c.model,'piper:en_US-ljspeech-high')
        with self.assertRaisesRegex(ValueError,'piper_python_required'):c.require_ready()
        with self.assertRaisesRegex(ValueError,'piper_model_required'):replace(c,piper_python=sys.executable).require_ready()
        self.assertEqual(TTSConfig.from_env({}).provider,'google')
        for bad in ('auto','Piper'):
            with self.assertRaises(ValueError):TTSConfig.from_env({'TTS_PROVIDER':bad})

class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self,kind):
        root=Path(__file__).resolve().parents[1];(root/'.tmp').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root/'.tmp') as temp:
            model=Path(temp)/'en_US-ljspeech-high.onnx';model.write_bytes(b'fixture');Path(str(model)+'.json').write_text('{}')
            config=TTSConfig(provider='piper',piper_python=sys.executable,piper_model=str(model),timeout=.03 if kind=='timeout' else 2)
            script=Path(temp)/'worker.py'
            if kind in {'timeout','cancel'}:body='import time;time.sleep(15)'
            elif kind=='bad':body='print("bad-json")'
            elif kind=='error':body='import sys;print(\'{"sample_rate":22050,"channels":1,"sample_width":2}\',flush=True);sys.exit(2)'
            elif kind=='odd':body='import sys;sys.stdout.buffer.write(b\'{"sample_rate":22050,"channels":1,"sample_width":2}\\nX\')'
            else:body='import sys,json,os;v=json.loads(sys.stdin.buffer.readline());assert v["text"]=="Private fixture";assert not os.getenv("GOOGLE_API_KEY");sys.stdout.buffer.write(b\'{"sample_rate":22050,"channels":1,"sample_width":2}\\n\'+bytes(44100))'
            script.write_text(body)
            with patch('speech_mcp.piper_adapter.WORKER',script),patch.dict(os.environ,{'GOOGLE_API_KEY':'never-forward-this'}):
                a=make_tts_adapter(config);self.assertIsInstance(a,PiperTTS)
                if kind=='cancel':
                    task=asyncio.create_task(a.synthesize('Private fixture','normal'))
                    while a.process is None:await asyncio.sleep(.001)
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):await task
                elif kind=='timeout':
                    with self.assertRaisesRegex(TTSProviderError,'tts_timeout'):await a.synthesize('Private fixture','normal')
                elif kind=='error':
                    with self.assertRaisesRegex(TTSProviderError,'local_tts_process_failed'):await a.synthesize('Private fixture','normal')
                elif kind in {'bad','odd'}:
                    with self.assertRaises(ValueError):await a.synthesize('Private fixture','normal')
                else:
                    pcm=await a.synthesize('Private fixture','normal');self.assertLessEqual(abs(len(pcm)-48000),4);self.assertEqual(a.metrics['native_sample_rate'],22050);self.assertGreater(a.metrics['backend_complete_seconds'],0)
                await a.close();self.assertIsNotNone(a.process.returncode)
    async def test_real_subprocess_pcm_and_secret_isolation(self):await self.exercise('ok')
    async def test_malformed_response(self):await self.exercise('bad')
    async def test_partial_sample(self):await self.exercise('odd')
    async def test_process_failure(self):await self.exercise('error')
    async def test_timeout_reaps_worker(self):await self.exercise('timeout')
    async def test_cancel_reaps_worker(self):await self.exercise('cancel')

class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_mcp_explicit_piper_contract_missing_setup_no_fallback(self):
        from speech_mcp.client import SpeechClients,SpeechServiceFailed
        class Events:
            def __init__(self,path):self.path=path;self.rows=[]
            def emit(self,event,**kw):self.rows.append({'event':event,**kw})
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(dir=root/'.tmp') as temp:
            events=Events(Path(temp)/'events.jsonl')
            with patch.dict(os.environ,{'TTS_PROVIDER':'piper','PIPER_PYTHON':'','PIPER_MODEL':'','GOOGLE_API_KEY':'','ALLOW_PROVIDER_AUDIO':'0'}):
                async with SpeechClients(events) as clients:
                    self.assertIsNone(clients.pacer)
                    tool=next(t for t in (await clients.sessions['tts'].list_tools()).tools if t.name=='synthesize')
                    self.assertEqual(set(tool.inputSchema['properties']),{'text','generation','request_id','style'})
                    with self.assertRaises(ValueError):await clients.call('tts',1,text='Test')
                    self.assertEqual(next(r for r in events.rows if r['event']=='mcp_request_error')['validation_reason'],'piper_python_required')
                    with self.assertRaises(SpeechServiceFailed):await clients.call('tts',2,text='Test')
            self.assertEqual(next(r for r in events.rows if r['event']=='mcp_connected' and r['role']=='tts')['provider'],'piper')

class CancellationShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_mcp_immediate_shutdown_after_cancel_consumes_reply(self):
        from voice_agent import EventLog
        from speech_mcp.client import SpeechClients
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(dir=root/'.tmp') as temp:
            events=EventLog(Path(temp)/'run')
            try:
                async with SpeechClients(events,module='tests.mcp_fixture_server') as clients:
                    task=asyncio.create_task(clients.call('asr',1,pcm_b64='synthetic-only'))
                    await asyncio.sleep(.05);task.cancel()
                    with self.assertRaises(asyncio.CancelledError):await task
                    # Deliberately no post-cancel sleep or extra RPC before context exit.
                self.assertFalse(clients.sessions['asr'].abandoned)
                rows=[json.loads(x) for x in events.path.read_text().splitlines()]
                self.assertTrue(any(x['event']=='mcp_cancelled_response_discarded' for x in rows))
                self.assertFalse(any(x['event']=='mcp_cancellation_settle_timeout' for x in rows))
            finally:events.close()
