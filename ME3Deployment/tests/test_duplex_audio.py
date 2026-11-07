import asyncio
from pathlib import Path
import tempfile
import unittest

from duplex_audio import PlayoutBuffer, ResponseFence, DuplexState
from voice_agent import Settings, EventLog, ROOT


class PlayoutSafetyTests(unittest.TestCase):
    def test_clear_discards_pending_audio_and_rejects_late_writer(self):
        q = PlayoutBuffer(16)
        self.assertEqual(q.offer(b"old-old", 0), "accepted")
        epoch, discarded = q.clear()
        self.assertEqual(discarded, 7)
        self.assertEqual(q.offer(b"late", 0), "stale")
        self.assertEqual(q.take(8), (bytes(8), epoch, 0, 0))
        self.assertEqual(q.offer(b"new", epoch), "accepted")
        self.assertEqual(q.take(4)[0], b"new\x00")

    def test_bounded_buffer_has_backpressure_without_overwriting(self):
        q = PlayoutBuffer(4)
        self.assertEqual(q.offer(b"1234", 0), "accepted")
        self.assertEqual(q.offer(b"X", 0), "full")
        self.assertEqual(q.take(3)[0], b"123")
        self.assertEqual(q.offer(b"AB", 0), "accepted")
        self.assertEqual(q.take(4)[0], b"4AB\x00")

    def test_partial_callback_preserves_sample_order(self):
        q = PlayoutBuffer(20)
        q.offer(b"abcd", 0)
        q.offer(b"efgh", 0)
        self.assertEqual(q.take(6)[0], b"abcdef")
        self.assertEqual(q.take(6)[0], b"gh" + bytes(4))

    def test_late_response_audio_is_fenced_until_fresh_turn(self):
        f = ResponseFence()
        self.assertTrue(f.response_start())
        f.speech_start()
        f.interrupt()
        self.assertFalse(f.accept_audio())
        self.assertFalse(f.response_start())
        self.assertFalse(f.accept_audio())
        f.speech_stop()
        self.assertTrue(f.response_start())
        self.assertTrue(f.accept_audio())
        self.assertEqual(f.generation, 2)

    def test_response_start_during_user_speech_never_reopens_audio(self):
        f = ResponseFence()
        f.speech_start()
        self.assertFalse(f.response_start())
        self.assertFalse(f.accept_audio())


class CallbackTelemetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_audio_thread_metadata_can_be_delivered_with_keyword_fields(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as temp:
            events = EventLog(Path(temp) / "run")
            state = DuplexState(Settings(api_key=""), events)
            state.loop = asyncio.get_running_loop()
            await asyncio.to_thread(state.post, "callback_test", epoch=3)
            await asyncio.sleep(0)
            events.close()
            self.assertIn('"epoch": 3', events.path.read_text())


if __name__ == "__main__":
    unittest.main()
