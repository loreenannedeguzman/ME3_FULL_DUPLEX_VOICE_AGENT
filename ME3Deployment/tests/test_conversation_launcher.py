import importlib.util,io,unittest,urllib.error
from pathlib import Path
from unittest.mock import patch
P=Path(__file__).resolve().parents[1]/'scripts/open_assistant_gui.py'
spec=importlib.util.spec_from_file_location('conversation_launcher',P);launcher=importlib.util.module_from_spec(spec);spec.loader.exec_module(launcher)

class LauncherTests(unittest.TestCase):
    def run_check(self,body):
        class Opener:
            def open(self,*args,**kw):return io.BytesIO(body)
        with patch.object(launcher.urllib.request,'build_opener',return_value=Opener()):return launcher.running()
    def test_deployed_interface_is_recognized_without_opening_audio_or_browser(self):
        root=P.parents[1];self.assertTrue(self.run_check((root/'me3_assistant/web/index.html').read_bytes()))
    def test_generic_success_or_partial_markers_do_not_identify_the_application(self):
        for body in [b'<title>ME3</title>',b'<title>Unrelated</title>',b'<title>ME3</title><form id="composer">']:
            with self.subTest(body=body),self.assertRaises(RuntimeError):self.run_check(body)
    def test_absent_listener_returns_false_without_spawning(self):
        with patch.object(launcher.urllib.request,'build_opener') as build,patch.object(launcher.subprocess,'Popen') as spawn:
            build.return_value.open.side_effect=urllib.error.URLError('refused')
            self.assertFalse(launcher.running());spawn.assert_not_called()

if __name__=='__main__':unittest.main()
