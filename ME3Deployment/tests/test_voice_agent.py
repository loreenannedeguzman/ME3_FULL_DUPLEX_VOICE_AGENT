import json
from pathlib import Path
import tempfile
import unittest

from voice_agent import EventLog, Settings, ROOT


class StartupSafetyTests(unittest.TestCase):
    def test_live_blocks_missing_credentials_and_audio_consent(self):
        with self.assertRaisesRegex(ValueError, "GOOGLE_API_KEY.*ALLOW_PROVIDER_AUDIO"):
            Settings.load({}, live=True)

    def test_key_alone_does_not_authorize_audio_or_model(self):
        with self.assertRaisesRegex(ValueError, "GEMINI_MODEL.*ALLOW_PROVIDER_AUDIO"):
            Settings.load({"GOOGLE_API_KEY": "test-not-a-real-key"}, live=True)

    def test_offline_diagnostics_do_not_need_api(self):
        self.assertEqual(Settings.load({}).max_seconds, 180)

    def test_bounded_session_and_invalid_devices(self):
        for env in ({"MAX_SESSION_SECONDS": "0"}, {"MAX_SESSION_SECONDS": "601"},
                    {"INPUT_DEVICE_INDEX": "-1"}, {"OUTPUT_DEVICE_INDEX": "oops"}):
            with self.subTest(env=env), self.assertRaises(ValueError):
                Settings.load(env)

    def test_secret_not_in_settings_representation(self):
        self.assertNotIn("private-test-value", repr(Settings(api_key="private-test-value")))

    def test_events_have_monotonic_order_and_no_audio_payload(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as temp:
            log = EventLog(Path(temp) / "run")
            log.emit("start")
            log.emit("end")
            log.close()
            rows = [json.loads(line) for line in log.path.read_text().splitlines()]
            self.assertLessEqual(rows[0]["monotonic_ns"], rows[1]["monotonic_ns"])
            self.assertTrue(all("audio" not in row and "transcript" not in row for row in rows))


if __name__ == "__main__":
    unittest.main()
