"""Benchmark guards and real-event accounting fixtures; no acoustic claims."""
import json,tempfile,unittest,hashlib
from pathlib import Path
from unittest.mock import patch
from benchmark_agent.protocol import SESSIONS,TRIALS,LIMITS
from benchmark_agent.console import authorization,Controller,verify_sources
from benchmark_agent.analyze import analyze
from benchmark_m6.protocol import SESSIONS as ORIGINAL

class ProtocolTests(unittest.TestCase):
    def test_twenty_normal_scripts_and_nondestructive_task(self):
        self.assertEqual(len(TRIALS),31);self.assertEqual(sum(t['kind']=='normal' for t in TRIALS.values()),20)
        for name in 'ABCD':self.assertEqual(SESSIONS[name],ORIGINAL[name])
        self.assertIn('currently saved',SESSIONS['F'][-1]['script'][0]);self.assertEqual(LIMITS['commit_item'],0)
    def test_old_or_unconfirmed_allowance_cannot_initialize(self):
        with tempfile.TemporaryDirectory() as tmp,patch('benchmark_agent.console.PHASE',Path(tmp)):
            path=Path(tmp)/'authorization-run-20261020-001.json';data={'approved':True,'run_id':'run-20261020-001','limits':LIMITS,'existing_google_allowance_confirmed':False};path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):authorization('run-20261020-001')
            data['existing_google_allowance_confirmed']=True;path.write_text(json.dumps(data));self.assertEqual(authorization('run-20261020-001')['limits'],LIMITS)
    def test_changed_runtime_rejected_before_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);frozen=root/'freeze';frozen.mkdir();(root/'runtime.py').write_text('old')
            (frozen/'source.json').write_text(json.dumps({'source_hashes':{'runtime.py':hashlib.sha256(b'old').hexdigest()}}))
            with patch('benchmark_agent.console.ROOT',root),patch('benchmark_agent.console.FROZEN',frozen):
                verify_sources();(root/'runtime.py').write_text('new')
                with self.assertRaisesRegex(ValueError,'frozen_runtime'):verify_sources()
    def test_reset_action_unavailable_and_readback_requires_actual_tool(self):
        c=Controller('run-20261020-001')
        with self.assertRaisesRegex(ValueError,'no_deletions'):c.action({'action':'prepare_task'})
        with patch('benchmark_agent.console.rows',return_value=[]),patch('benchmark_agent.console.task_state') as state:
            with self.assertRaisesRegex(ValueError,'no_F02_actual'):c.action({'action':'verify_task'})
            state.assert_not_called()
    def test_model_and_rpc_complete_cancelled_incomplete_are_distinct(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'A').mkdir();(root/'B').mkdir()
            manifest={'protocol':{'A':[{'id':'A01','kind':'normal'}],'B':[{'id':'B01','kind':'normal'}]},'source':{'revision':'runtime'},'harness':{'revision':'harness'},'environment':{},'methods':{}}
            (root/'manifest.json').write_text(json.dumps(manifest))
            def row(event,t,**kw):return {'event':event,'monotonic_ns':int(t*1e9),'session_id':'A','trial_id':'A01',**kw}
            events=[row('m6_trial_start',0),row('ba_llm_start',1,step_id='1',generation=1),row('ba_llm_end',2,step_id='1',generation=1),row('task_mcp_request_start',2,request_id='r',tool='get_current_time',generation=1),row('task_mcp_request_cancelled',3,request_id='r'),row('ba_llm_start',4,step_id='2',generation=2)]
            (root/'A/events.jsonl').write_text('\n'.join(json.dumps(x) for x in events));out=analyze(root)
            self.assertEqual(out['accounting']['outcomes'],{'incomplete':1,'unstarted':1});self.assertEqual(out['LLM_complete_request']['n'],1);self.assertEqual(out['task_RPC_complete_request']['n'],0)
            self.assertEqual([x['state'] for x in out['agent_model_and_tool_requests']],['complete','canceled','incomplete']);self.assertEqual(out['LLM_TTFT']['status'],'NOT_MEASURED');self.assertIsNone(out['LLM_complete_request']['p95_seconds'])

class RunnerGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_runner_cannot_bypass_frozen_source_or_open_audio(self):
        from benchmark_agent.runner import run
        with patch('benchmark_agent.console.verify_sources',return_value={}),patch('benchmark_agent.console.authorization',side_effect=ValueError('fresh_allowance_required')),patch('benchmark_agent.runner.Settings.load') as settings:
            with self.assertRaisesRegex(ValueError,'fresh_allowance'):await run('run-20261020-001','A')
            settings.assert_not_called()

class GatewayAccountingTests(unittest.TestCase):
    def test_upstream_504_is_a_timeout_failure_with_no_completed_asr_latency(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'A').mkdir();manifest={'protocol':{'A':[{'id':'A01','kind':'normal'}]},'source':{'revision':'runtime'},'harness':{'revision':'harness'},'environment':{},'methods':{}}
            (root/'manifest.json').write_text(json.dumps(manifest))
            events=[{'event':'m6_trial_start','monotonic_ns':0,'trial_id':'A01'},{'event':'mcp_request_start','monotonic_ns':1000000000,'trial_id':'A01','request_id':'gateway','role':'asr','generation':1},{'event':'mcp_request_error','monotonic_ns':3000000000,'trial_id':'A01','request_id':'gateway','role':'asr','generation':1,'code':504,'error_type':'ServerError'},{'event':'m6_trial_report','monotonic_ns':4000000000,'trial_id':'A01','outcome':'fail','audible':False,'response_correct':False}]
            (root/'A/events.jsonl').write_text('\n'.join(json.dumps(x) for x in events));out=analyze(root)
            self.assertEqual(out['accounting']['timeout_trials'],1);self.assertEqual(out['trials'][0]['state'],'fail');self.assertEqual(out['all_stages'][0]['state'],'timeout');self.assertIsNone(out['all_stages'][0]['complete_seconds'])
