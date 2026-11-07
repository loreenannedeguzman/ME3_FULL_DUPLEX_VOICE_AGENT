import json,tempfile,unittest
from pathlib import Path
from benchmark_m6.analyze_v2 import analyze,terminal_stage
ROOT=Path(__file__).resolve().parents[1]
class CorrectedAnalysisTests(unittest.TestCase):
    def write_rows(self,folder,session,events):
        sub=folder/session;sub.mkdir();data=[{'event':event,'monotonic_ns':int(ms*1e6),'session_id':session,**kw} for event,ms,kw in events];(sub/'events.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in data),encoding='utf-8')
    def test_provider_timeout_without_operator_report_is_timeout_and_censored(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as temp:
            folder=Path(temp);base={'trial_id':'A02','generation':1};self.write_rows(folder,'A',[('m6_trial_start',1,base),('m6_speech_onset_proxy',2,base),('m6_endpoint',3,base),('mcp_request_start',4,{**base,'role':'asr','request_id':'a'}),('mcp_request_error',30004,{**base,'role':'asr','request_id':'a','error_type':'TimeoutError'})]);result=analyze(folder);trial=next(t for t in result['trials'] if t['trial_id']=='A02');stage=result['attempts'][0]['stages']['asr']
            self.assertEqual(trial['outcome'],'timeout');self.assertEqual(result['trial_accounting']['timeout_rate_of_started'],1);self.assertEqual(stage['state'],'timeout');self.assertEqual(stage['elapsed_to_terminal_ms'],30000);self.assertIsNone(stage['complete_ms']);self.assertIsNone(result['attempts'][0]['asr_ms']);self.assertEqual(result,analyze(folder))
    def test_time_limit_cancellation_remains_incomplete_without_timeout(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as temp:
            folder=Path(temp);base={'trial_id':'B05','generation':1};self.write_rows(folder,'B',[('m6_trial_start',1,base),('m6_speech_onset_proxy',2,base),('mcp_request_start',4,{**base,'role':'asr','request_id':'b'}),('mcp_request_cancelled',14,{**base,'role':'asr','request_id':'b'}),('session_stop',15,{'trial_id':'B05','reason':'time_limit'})]);result=analyze(folder);trial=next(t for t in result['trials'] if t['trial_id']=='B05');self.assertEqual(trial['outcome'],'incomplete');self.assertFalse(trial['timeout']);self.assertEqual(result['attempts'][0]['stages']['asr']['elapsed_to_terminal_ms'],10)
    def test_stage_error_is_not_complete_or_hidden_retry(self):
        start={'event':'mcp_request_start','monotonic_ns':1,'request_id':'x'};end={'event':'mcp_request_error','monotonic_ns':1000001,'request_id':'x','error_type':'AuthenticationError'}
        stage=terminal_stage([end],start,'mcp_request_end','mcp_request_cancelled','mcp_request_error','x');self.assertEqual(stage['state'],'error');self.assertIsNone(stage['complete_ms']);self.assertTrue(stage['censored']);self.assertEqual(stage['elapsed_to_terminal_ms'],1)
    def test_independent_verification_from_different_boot_cannot_prove_timing_or_pass(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as temp:
            folder=Path(temp);base={'trial_id':'F02','generation':1};self.write_rows(folder,'F',[('m6_clock',0,{'boot_id':'one'}),('m6_trial_start',1,base),('m6_speech_onset_proxy',2,base),('response_complete',3,base),('task_human_confirmation',4,base),('task_mcp_request_end',5,{**base,'tool':'commit_item'}),('m6_task_external_verification',6,{**base,'verified':True,'verifier_boot_id':'two'}),('m6_trial_report',7,{'trial_id':'F02','outcome':'pass','audible':True,'response_correct':True})]);result=analyze(folder);trial=next(t for t in result['trials'] if t['trial_id']=='F02');self.assertEqual(trial['outcome'],'fail');self.assertFalse(trial['checks']['independent_verifier_same_boot']);self.assertIsNone(trial['task_timing']['commit_to_independent_verification_ms'])
