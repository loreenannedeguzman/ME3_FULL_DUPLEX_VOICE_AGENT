import copy,unittest
from me3_m7.analyze import matched_comparison,duration
class MatchedBenchmarkTests(unittest.TestCase):
    def data(self):
        return {'trials':[{'trial_id':'A01','kind':'normal','attempt_count':1,'outcome':'pass'},{'trial_id':'A02','kind':'normal','attempt_count':1,'outcome':'timeout'}],'attempts':[{'trial_id':'A01','tts_cold_start':False,'response_complete':True,'onset_ns':0,'endpoint_ns':1000000000,'asr_ms':100,'speech_end_endpoint_to_submission_ms':200,'speech_onset_vad_proxy_to_submission_ms':1200,'reasoning_request_ms':50,'tts_ms':50}]}
    def test_same_script_intersection_only(self):
        a=self.data();b=copy.deepcopy(a);b['attempts'][0]['asr_ms']=80;x=matched_comparison(a,b);self.assertEqual(x['n'],1);self.assertEqual(x['matched_ids'],['A01']);self.assertEqual(x['metrics']['asr_ms']['paired_delta_m7_minus_m6']['median_ms'],-20)
    def test_failed_or_segmented_case_cannot_create_fast_improvement(self):
        for change in ({'outcome':'fail'},{'attempt_count':2}):
            a=self.data();b=copy.deepcopy(a);b['trials'][0].update(change);x=matched_comparison(a,b);self.assertEqual(x['n'],0);self.assertFalse(x['all_baseline_cases_matched']);self.assertEqual(x['unmatched_baseline_ids'],['A01'])
    def test_reducer_does_not_modify_input(self):
        a=self.data();before=copy.deepcopy(a);matched_comparison(a,a);self.assertEqual(a,before)

class TraceClockTests(unittest.TestCase):
    def test_server_observed_only_and_client_fallback_clocks(self):
        self.assertEqual(duration({'observed_ns':1000000},{'observed_ns':3000000}),2)
        self.assertEqual(duration({'monotonic_ns':1000000},{'callback_ns':3000000}),2)
        self.assertEqual(duration({'observed_ns':1000000,'monotonic_ns':9},{'observed_ns':3000000}),2)
    def test_missing_negative_and_unobserved_are_not_zero(self):
        self.assertIsNone(duration(None,{'observed_ns':3}))
        self.assertIsNone(duration({'observed_ns':3},{'observed_ns':1}))
        with self.assertRaises(ValueError):duration({}, {'observed_ns':3})

class SupplementaryBudgetTests(unittest.TestCase):
    def setUp(self):
        import tempfile,json,hashlib
        from pathlib import Path
        from unittest.mock import patch
        import me3_m7.benchmark_console as c
        self.c=c;self.temp=tempfile.TemporaryDirectory(dir=c.ROOT/'.tmp');self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        (self.root/'config').mkdir();(self.root/'config/m7_asr.json').write_text('{}');(self.root/'evidence/m7').mkdir(parents=True);(self.root/'tiny.py').write_text('pass')
        self.source='source-recovery-001.json';(self.root/'evidence/m7'/self.source).write_text(json.dumps({'revision':'fixture','source_hashes':{'tiny.py':hashlib.sha256(b'pass').hexdigest()}}))
        self.prior=self.root/'prior';self.prior.mkdir();self.budget={'asr':35,'reasoning':27,'tts':37,'commit_item':1};(self.prior/'budget.json').write_text(json.dumps(self.budget))
        for item in (patch.object(c,'ROOT',self.root),patch.object(c,'run_path',lambda name:self.root/name),patch.object(c.old,'environment',lambda:{}),patch.object(c.old,'task_state',lambda:(None,[{'item':'pencils','quantity':5,'operation_id':'p'}],{'state_digests':{'p':'d'}}))):item.start();self.addCleanup(item.stop)
    def test_supplement_inherits_consumed_allowance_including_commit(self):
        self.c.init('supplement',self.source,'prior');self.assertEqual(self.c.read(self.root/'supplement/budget.json'),self.budget)
        from benchmark_m6.storage import Budget
        with self.assertRaises(ValueError):Budget(self.root/'supplement/budget.json',self.c.LIMITS).reserve('commit_item')
    def test_invalid_or_missing_inherited_counters_cannot_reset_quota(self):
        import json
        for i,bad in enumerate(({'asr':35},{**self.budget,'asr':49},{**self.budget,'tts':-1})):
            (self.prior/'budget.json').write_text(json.dumps(bad))
            with self.assertRaises(ValueError):self.c.init('invalid'+str(i),self.source,'prior')
            self.assertFalse((self.root/('invalid'+str(i))/'budget.json').exists())
    def test_source_selector_rejects_paths_outside_m7(self):
        with self.assertRaisesRegex(ValueError,'manifest'):self.c.init('unsafe','../outside.json','prior')
