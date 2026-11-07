import asyncio,json,os,sys,tempfile,unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from speech_mcp.tts_config import TTSConfig
from speech_mcp.tts_adapters import make_tts_adapter,TTSProviderError
from speech_mcp.piper_resident import ResidentPiperTTS
ROOT=Path(__file__).resolve().parents[1]
FIXTURE=Path(__file__).with_name('resident_fixture_worker.py')

class ReuseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'.tmp');self.folder=Path(self.temp.name)
        model=self.folder/'en_US-ljspeech-high.onnx';model.write_bytes(b'fixture');Path(str(model)+'.json').write_text('{}',encoding='utf-8')
        self.config=TTSConfig(provider='piper',piper_python=sys.executable,piper_model=str(model),piper_worker_mode='resident',timeout=3)
        self.patch=patch('speech_mcp.piper_resident.WORKER',FIXTURE);self.patch.start()
        self.adapter=make_tts_adapter(self.config);self.assertIsInstance(self.adapter,ResidentPiperTTS)
    async def asyncTearDown(self):
        await self.adapter.close();self.patch.stop();self.temp.cleanup()
    async def test_config_explicit_and_legacy_preserved(self):
        self.assertEqual(TTSConfig.from_env({'TTS_PROVIDER':'piper'}).piper_worker_mode,'per_request')
        self.assertEqual(TTSConfig.from_env({'TTS_PROVIDER':'piper','PIPER_WORKER_MODE':'resident'}).piper_worker_mode,'resident')
        for bad in ('auto','','Resident'):
            with self.assertRaisesRegex(ValueError,'invalid_piper_worker_mode'):TTSConfig.from_env({'TTS_PROVIDER':'piper','PIPER_WORKER_MODE':bad})
    async def test_one_process_many_requests_and_utf8_secret_isolation(self):
        with patch.dict(os.environ,{'GOOGLE_API_KEY':'must-not-leave-parent'}):
            pcm=await self.adapter.synthesize_identified('Hello','normal',1,'request-a');pid=self.adapter.process.pid
            self.assertTrue(self.adapter.metrics['cold_start']);self.assertEqual(self.adapter.metrics['reuse_index'],0)
            for gen in (2,3):
                self.assertEqual(await self.adapter.synthesize_identified('Hello '+chr(0x1F600)*690,'slow',gen,'request-b'),pcm)
                self.assertEqual(self.adapter.process.pid,pid);self.assertEqual(self.adapter.metrics['reuse_index'],gen-1);self.assertFalse(self.adapter.metrics['cold_start'])
    async def test_concurrent_calls_serialize_not_cross_audio(self):
        a,b=await asyncio.gather(self.adapter.synthesize_identified('One','normal',1,'one'),self.adapter.synthesize_identified('Two','normal',2,'two'))
        self.assertEqual(a,b);self.assertEqual(self.adapter.epoch,1);self.assertEqual(self.adapter.metrics['reuse_index'],1)
    async def test_active_cancellation_reaps_then_fresh_request_recovers(self):
        await self.adapter.synthesize('warm','normal');old=self.adapter.process
        task=asyncio.create_task(self.adapter.synthesize('sleep','normal'));await asyncio.sleep(.05);task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertIsNotNone(old.returncode);self.assertIsNone(self.adapter.process)
        await self.adapter.synthesize('replacement','normal');self.assertNotEqual(old.pid,self.adapter.process.pid);self.assertEqual(self.adapter.epoch,2);self.assertTrue(self.adapter.metrics['cold_start'])
        await self.adapter.synthesize('follow up','normal');self.assertEqual(self.adapter.epoch,2);self.assertFalse(self.adapter.metrics['cold_start'])
    async def test_cancel_lock_waiter_preserves_active_worker(self):
        await self.adapter.synthesize('warm','normal');old=self.adapter.process
        await self.adapter.lock.acquire()
        task=asyncio.create_task(self.adapter.synthesize('waiting','normal'));await asyncio.sleep(.01);task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.adapter.lock.release();self.assertIs(self.adapter.process,old);self.assertIsNone(old.returncode)
        await self.adapter.synthesize('next','normal');self.assertEqual(self.adapter.epoch,1)
    async def test_timeout_destroys_worker_no_hidden_retry(self):
        await self.adapter.synthesize('warm','normal');old=self.adapter.process
        self.adapter.config=replace(self.config,timeout=.08)
        with self.assertRaisesRegex(TTSProviderError,'tts_timeout'):await self.adapter.synthesize('sleep','normal')
        self.assertIsNotNone(old.returncode);self.assertIsNone(self.adapter.process);self.assertEqual(self.adapter.epoch,1)
    async def test_all_protocol_failures_retire_worker(self):
        for text in ('identity','malformed','oversize','truncated','odd','end-size','die'):
            with self.subTest(text=text):
                await self.adapter.synthesize('warm','normal');old=self.adapter.process
                with self.assertRaises((ValueError,TTSProviderError)):await self.adapter.synthesize(text,'normal')
                self.assertIsNone(self.adapter.process);self.assertIsNotNone(old.returncode)
    async def test_close_reaps_idle_process_and_prevents_restart(self):
        await self.adapter.synthesize('warm','normal');old=self.adapter.process
        await self.adapter.close();self.assertIsNotNone(old.returncode)
        with self.assertRaisesRegex(ValueError,'piper_worker_closed'):await self.adapter.synthesize('next','normal')
    async def test_cancel_during_startup_keeps_handle_for_reaping(self):
        from speech_mcp import piper_resident
        original=asyncio.create_subprocess_exec;created=asyncio.Event();procs=[]
        async def slow(*args,**kwargs):
            proc=await original(*args,**kwargs);procs.append(proc);created.set();await asyncio.sleep(.05);return proc
        with patch.object(piper_resident.asyncio,'create_subprocess_exec',slow):
            task=asyncio.create_task(self.adapter.synthesize('hello','normal'));await created.wait();task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
        self.assertTrue(procs);self.assertTrue(all(p.returncode is not None for p in procs));self.assertIsNone(self.adapter.process)
    async def test_real_mcp_reuse_cancellation_replacement_shutdown(self):
        from voice_agent import EventLog
        from speech_mcp.client import SpeechClients
        events=EventLog(self.folder/'mcp');pids=[]
        env={'TTS_PROVIDER':'piper','PIPER_WORKER_MODE':'resident','PIPER_PYTHON':sys.executable,'PIPER_MODEL':self.config.piper_model,'GOOGLE_API_KEY':'','ALLOW_PROVIDER_AUDIO':'0'}
        try:
            with patch.dict(os.environ,env):
                async with SpeechClients(events,module='tests.resident_mcp_fixture_server') as clients:
                    for gen in (1,2):
                        d=await clients.call('tts',gen,text='warm',style='normal');pids.append(d['tts_metrics']['worker_pid']);self.assertEqual(d['generation'],gen)
                    self.assertEqual(pids[0],pids[1]);self.assertEqual(d['tts_metrics']['reuse_index'],1)
                    task=asyncio.create_task(clients.call('tts',3,text='sleep',style='normal'));await asyncio.sleep(.05);task.cancel()
                    with self.assertRaises(asyncio.CancelledError):await task
                    d=await clients.call('tts',4,text='replacement',style='normal');pids.append(d['tts_metrics']['worker_pid']);self.assertNotEqual(pids[0],pids[2]);self.assertEqual(d['tts_metrics']['worker_epoch'],2)
                    task=asyncio.create_task(clients.call('tts',5,text='sleep',style='normal'));await asyncio.sleep(.05);task.cancel()
                    with self.assertRaises(asyncio.CancelledError):await task
                self.assertFalse(clients.sessions['tts'].abandoned)
            # Standard-library OS probes, no additional runtime dependency.
            def running(pid):
                if os.name!='nt':return Path('/proc',str(pid)).exists()
                import ctypes
                kernel=ctypes.WinDLL('kernel32',use_last_error=True)
                kernel.OpenProcess.restype=ctypes.c_void_p
                kernel.GetExitCodeProcess.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_ulong)]
                kernel.CloseHandle.argtypes=[ctypes.c_void_p]
                handle=kernel.OpenProcess(0x1000,False,pid)
                if not handle:return False
                try:
                    code=ctypes.c_ulong();ok=kernel.GetExitCodeProcess(handle,ctypes.byref(code));return bool(ok and code.value==259)
                finally:kernel.CloseHandle(handle)
            self.assertTrue(all(not running(pid) for pid in set(pids)))
        finally:events.close()
