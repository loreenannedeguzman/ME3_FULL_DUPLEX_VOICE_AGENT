import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from me3_completion.live_runner import Window,run_path
from me3_completion.scenarios import LIMITS,SESSIONS
from benchmark_m6.storage import Budget,read
from speech_mcp.client import ProviderLimited
from tests.test_completion import Events
class ControlsTests(unittest.TestCase):
    def test_global_rate_window_survives_session_change(self):
        with tempfile.TemporaryDirectory() as temp,patch('me3_completion.live_runner.PHASE',Path(temp)):
            a=Window(Events());b=Window(Events())
            with patch('me3_completion.live_runner.time.monotonic',return_value=1):
                for _ in range(14):a.reserve()
                with self.assertRaises(ProviderLimited):b.reserve()
            with patch('me3_completion.live_runner.time.monotonic',return_value=62):b.reserve()
            self.assertEqual(read(Path(temp)/'functional-rate.json'),[62])
    def test_shared_budget_counts_canceled_requests_without_refund(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'budget.json';a=Budget(path,LIMITS);b=Budget(path,LIMITS)
            for _ in range(48):a.reserve('asr')
            with self.assertRaises(ValueError):b.reserve('asr')
            a.reserve('commit_item')
            with self.assertRaises(ValueError):b.reserve('commit_item')
            self.assertEqual(read(path)['asr'],48)
    def test_run_path_rejects_other_locations(self):
        for name in ['../x','core-20261020-001','functional-20261020-001/x','functional-20261020-1']:
            with self.assertRaises(ValueError):run_path(name)
        self.assertEqual(run_path('functional-20261020-001').name,'functional-20261020-001')
    def test_all_scenarios_have_cue_expected_and_defined_dimensions(self):
        ids=[]
        for name,episodes in SESSIONS.items():
            for episode,definition in episodes.items():
                self.assertTrue(episode.startswith(name));self.assertEqual(set(definition),{'dimension','cue','expected'});ids.append(episode)
        self.assertEqual(len(ids),len(set(ids)));self.assertIn('E2',ids)
