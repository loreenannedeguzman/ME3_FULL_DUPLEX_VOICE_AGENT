import asyncio,threading,unittest
from unittest.mock import patch
from me3_assistant.gui import Console,row
from tests import test_assistant_gui as http_tests
from tests.test_assistant_mode import config

class StateThreadsTests(unittest.TestCase):
    setUp=http_tests.HTTPTests.setUp;tearDown=http_tests.HTTPTests.tearDown;request=http_tests.HTTPTests.request;auth=http_tests.HTTPTests.auth
    def test_real_HTTP_reads_live_conversation_on_owner_loop_not_status_worker(self):
        cfg=config();cfg['sha256']='fixture';c=Console(cfg,{'revision':'fixture'});owner=self.loop
        class Control:
            @property
            def history(self):
                if asyncio.get_running_loop() is not owner:raise AssertionError('conversation_read_outside_owner_loop')
                return [row('user','A fixture conversation')]
        c.controller=Control();self.server.console=c
        with patch.object(c,'gate',return_value=({'backend':'groq'},{})):
            status,body,_=self.request('GET','/api/state',Cookie='me3_gui='+self.server.cookie)
        self.assertEqual(status,200);self.assertIn(b'A fixture conversation',body)
    def test_blocked_source_checks_leave_real_console_stop_and_loop_responsive(self):
        cfg=config();cfg['sha256']='fixture';c=Console(cfg,{'revision':'fixture'});self.server.console=c;entered=threading.Event();release=threading.Event();heartbeat=threading.Event();result=[]
        original=getattr(c,'status_checks',None)
        self.assertIsNotNone(original,'separate status I/O from live projection')
        def slow():entered.set();release.wait(2);return ({k:0 for k in ['asr','reasoning','tts','commit_item']},{k:1 for k in ['asr','reasoning','tts','commit_item']},{}, {})
        def poll():result.append(self.request('GET','/api/state',Cookie='me3_gui='+self.server.cookie)[0])
        with patch.object(c,'status_checks',slow):
            thread=threading.Thread(target=poll);thread.start()
            try:
                self.assertTrue(entered.wait(1));self.loop.call_soon_threadsafe(heartbeat.set);self.assertTrue(heartbeat.wait(.4))
                self.assertEqual(self.request('POST','/api/command','{"action":"stop"}',**self.auth())[0],200)
            finally:release.set();thread.join(3)
        self.assertEqual(result,[200]);self.assertEqual(c.state,'stopped')
