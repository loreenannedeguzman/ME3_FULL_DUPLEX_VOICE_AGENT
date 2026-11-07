import asyncio,shutil,struct,unittest,wave,io
from unittest.mock import patch
from me3_m7.codec import encode,decode,run_ffmpeg
class CodecTests(unittest.IsolatedAsyncioTestCase):
    async def test_wav_preserves_rate_channels_and_every_sample(self):
        pcm=bytes(range(256))*10;data,mime=await encode(pcm,'wav')
        with wave.open(io.BytesIO(data)) as w:self.assertEqual((w.getframerate(),w.getnchannels(),w.getsampwidth()),(16000,1,2));self.assertEqual(w.readframes(w.getnframes()),pcm)
        self.assertEqual(mime,'audio/wav')
    @unittest.skipUnless(shutil.which('ffmpeg'),'native FFmpeg unavailable on this host; target roundtrip required')
    async def test_native_flac_is_lossless_with_unchanged_samples(self):
        pcm=b''.join(struct.pack('<h',(i%100)*100-5000) for i in range(16000));data,mime=await encode(pcm,'flac');self.assertEqual(mime,'audio/flac');self.assertEqual(await decode(data),pcm);self.assertLess(len(data),len(pcm))
    async def test_invalid_format_or_audio_never_starts_process(self):
        with patch('me3_m7.codec.asyncio.create_subprocess_exec') as spawn:
            for pcm,fmt in [(b'','wav'),(b'x','flac'),(bytes(480002),'flac'),(bytes(32),'automatic')]:
                with self.assertRaises(ValueError):await encode(pcm,fmt)
            spawn.assert_not_called()
    async def exercise_cancel(self,during_start):
        started=asyncio.Event();waiting=asyncio.Event();state={}
        class Child:
            returncode=None
            async def communicate(self,data):waiting.set();await asyncio.Event().wait()
            def terminate(self):self.returncode=-15;state['terminated']=True
            def kill(self):self.returncode=-9
            async def wait(self):return self.returncode
        child=Child()
        async def spawn(*a,**kw):
            state['env']=kw['env'];started.set()
            if during_start:await asyncio.sleep(.05)
            return child
        with patch('me3_m7.codec.shutil.which',return_value='ffmpeg'),patch('me3_m7.codec.asyncio.create_subprocess_exec',side_effect=spawn),patch.dict('os.environ',{'GOOGLE_API_KEY':'fixture-secret'}):
            task=asyncio.create_task(run_ffmpeg(bytes(100),[]));await (started if during_start else waiting).wait();task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
        self.assertTrue(state['terminated']);self.assertNotIn('GOOGLE_API_KEY',state['env']);self.assertIsNotNone(child.returncode)
    async def test_cancellation_during_encode_reaps_child(self):await self.exercise_cancel(False)
    async def test_cancellation_during_spawn_retains_child_handle(self):await self.exercise_cancel(True)
