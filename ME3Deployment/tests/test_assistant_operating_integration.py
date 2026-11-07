import os,unittest
from unittest.mock import patch
from me3_assistant.runner import normal_settings
from voice_agent import Settings

class SessionSettingsTests(unittest.TestCase):
    def test_normal_hour_session_uses_validated_config_while_legacy_loader_stays_bounded(self):
        cfg={'runtime':{'max_session_seconds':3600}}
        with patch.dict(os.environ,{'MAX_SESSION_SECONDS':'3600'},clear=True),patch('me3_assistant.backends.check_ready'):
            settings=normal_settings(cfg,'groq')
            self.assertEqual(settings.max_seconds,3600)
            self.assertEqual(Settings.load().max_seconds,600)
            with self.assertRaises(ValueError):Settings.load({'MAX_SESSION_SECONDS':'3600'})

if __name__=='__main__':unittest.main()
