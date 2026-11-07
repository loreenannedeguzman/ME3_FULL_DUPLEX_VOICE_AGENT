import unittest,tempfile
from pathlib import Path
from unittest.mock import patch
from me3_core.live_runner import Window
from me3_core.scenarios import LIMITS,SESSIONS
from speech_mcp.client import ProviderLimited
from me3_core.events import CoreEvents
from voice_agent import ROOT
class ControlTests(unittest.TestCase):
    def test_combined_google_rate_guard_blocks_before_dispatch(self):
        rows=[]
        class Events:
            def emit(self,event,**kw):rows.append(event)
        window=Window(Events())
        with patch('me3_core.live_runner.time.monotonic',return_value=0):
            for _ in range(14):window.reserve()
            with self.assertRaises(ProviderLimited):window.reserve()
        self.assertEqual(len(window.rows),14);self.assertIn('core_local_rate_guard',rows)
        with patch('me3_core.live_runner.time.monotonic',return_value=61):window.reserve()
        self.assertEqual(len(window.rows),1)
    def test_old_turn_settlement_preserves_current_generation_and_episode(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as folder:
            events=CoreEvents(Path(folder)/'events')
            try:
                events.active='A1';events.emit('core_speech_onset',generation=1);events.active='A2';events.emit('core_speech_onset',generation=2);events.emit('core_turn_settled',generation=1,outcome='canceled')
                self.assertEqual(events.current_generation,2);self.assertEqual(events.generations[1],'A1');self.assertEqual(events.generations[2],'A2')
            finally:events.close()
    def test_actual_request_limits_are_shared_across_all_sessions(self):
        from benchmark_m6.storage import Budget,read
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as folder:
            path=Path(folder)/'budget.json';a=Budget(path,LIMITS);b=Budget(path,LIMITS)
            a.reserve('commit_item')
            with self.assertRaises(ValueError):b.reserve('commit_item')
            for _ in range(22):a.reserve('asr')
            with self.assertRaises(ValueError):b.reserve('asr')
            self.assertEqual(read(path)['asr'],22)
