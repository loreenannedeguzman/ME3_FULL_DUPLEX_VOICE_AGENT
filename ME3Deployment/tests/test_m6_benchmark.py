import asyncio,json,math,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from enum import Enum
from benchmark_m6.protocol import TRIALS,SESSIONS,LIMITS
from benchmark_m6.analyze import analyze,stats,duration
from benchmark_m6.runner import Events
from benchmark_m6.observe import attach
from benchmark_m6.storage import Budget,read,run_path
ROOT=Path(__file__).resolve().parents[1]

class ReducerTests(unittest.TestCase):
    def test_percentile_threshold_and_nearest_rank(self):
        self.assertIsNone(stats(range(19))['p95_ms']);self.assertEqual(stats(range(1,21))['p95_ms'],19);self.assertEqual(stats([None,1,3])['median_ms'],2)
    def test_observed_clock_precedes_delayed_logging(self):
        self.assertEqual(duration({'observed_ns':100,'monotonic_ns':1000},{'observed_ns':150,'monotonic_ns':9999}),.00005)
        self.assertIsNone(duration({'monotonic_ns':200},{'monotonic_ns':100}));self.assertIsNone(duration(None,{'monotonic_ns':1}))
    def test_plan_budgets_scripts_and_m4(self):
        self.assertEqual(len(TRIALS),31);self.assertEqual(sum(x['kind']=='normal' for x in TRIALS.values()),20)
        self.assertEqual([x['kind'] for x in SESSIONS['F']],['warmup','split_complete','task']);self.assertEqual(LIMITS['commit_item'],1)
    def fixture(self,folder,correct=True):
        sub=folder/'A';sub.mkdir();data=[]
        def emit(event,ms,**kw):data.append({'event':event,'monotonic_ns':int((ms+10000)*1e6),'observed_ns':int(ms*1e6),'session_id':'A','trial_id':'A01',**kw})
        emit('m6_trial_start',0,kind='normal');emit('m6_speech_onset_proxy',100,generation=1);emit('m6_endpoint',200,generation=1)
        emit('mcp_request_start',210,generation=1,role='asr',request_id='asr-a');emit('mcp_request_end',310,generation=1,role='asr',request_id='asr-a')
        emit('m6_reasoning_request_start',320,generation=1);emit('m6_reasoning_request_end',520,generation=1)
        emit('mcp_request_start',530,generation=1,role='tts',request_id='tts-a');emit('mcp_request_end',830,generation=1,role='tts',request_id='tts-a')
        emit('tts_backend_metrics',831,generation=1,cold_start=False,backend_first_pcm_seconds=.25)
        emit('m6_output_submission',850,epoch=1,nonzero=True,block_seconds=.02)
        emit('response_complete',950,generation=1,greeting=False)
        emit('m6_trial_report',960,outcome='pass' if correct else 'fail',audible=True,response_correct=correct)
        (sub/'events.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in data),encoding='utf-8');return data
    def test_stage_reconstruction_and_unstarted_ledger(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as temp:
            folder=Path(temp);self.fixture(folder);result=analyze(folder);a=result['attempts'][0]
            self.assertEqual(a['speech_end_endpoint_to_submission_ms'],650);self.assertEqual(a['speech_onset_vad_proxy_to_submission_ms'],750)
            self.assertEqual((a['asr_ms'],a['reasoning_request_ms'],a['tts_ms']),(100,200,300))
            self.assertEqual(result['trial_accounting']['outcomes'],{'unstarted':30,'pass':1});self.assertEqual(result['trial_accounting']['total'],31)
            self.assertEqual(result['physical_acoustic_metrics']['status'],'NOT_MEASURED');self.assertIsNone(result['interruption_summary']['onset_to_callback_epoch_stop_proxy_ms']['median_ms'])
            self.assertEqual(result,analyze(folder))
    def test_wrong_answer_keeps_available_latency_and_failure(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as temp:
            folder=Path(temp);self.fixture(folder,False);result=analyze(folder)
            self.assertEqual(result['trial_accounting']['failure_timeout_incomplete_rate_of_started'],1)
            self.assertEqual(result['steady_normal_all_available_including_semantic_failures']['tts_ms']['n'],1)
            self.assertEqual(result['steady_normal_successful_only']['tts_ms']['n'],0)
    def test_cancel_without_audio_retained_as_incomplete_not_zero(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as temp:
            folder=Path(temp);sub=folder/'F';sub.mkdir();events=[{'event':'m6_trial_start','trial_id':'F01','monotonic_ns':1},{'event':'m6_speech_onset_proxy','trial_id':'F01','generation':1,'monotonic_ns':2},{'event':'turn_task_cancelled','trial_id':'F01','generation':1,'monotonic_ns':3}]
            (sub/'events.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in events),encoding='utf-8');result=analyze(folder)
            self.assertEqual(result['trial_accounting']['outcomes']['incomplete'],1);self.assertTrue(result['attempts'][0]['canceled']);self.assertIsNone(result['attempts'][0]['tts_ms']);self.assertEqual(result['split_recovery']['success_rate'],0)
    def test_interruption_clocks_drain_estimate_and_capture(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as temp:
            folder=Path(temp);sub=folder/'E';sub.mkdir();data=[]
            def emit(event,ms,**kw):data.append({'event':event,'monotonic_ns':int((ms+10000)*1e6),'observed_ns':int(ms*1e6),'session_id':'E','trial_id':'E01',**kw})
            emit('m6_trial_start',0,kind='interrupt');emit('output_device_open',1,reported_output_latency_seconds=.05)
            emit('m6_speech_onset_proxy',100,generation=1);emit('m6_endpoint',200,generation=1);emit('generation_playout_started',990,generation=1)
            emit('m6_output_submission',1000,epoch=1,nonzero=True,block_seconds=.02)
            emit('m6_speech_onset_proxy',1010,generation=2);emit('m6_cancellation_request',1011,generation=1,replacement_generation=2)
            emit('playout_clear',1013,epoch=2,capture_count=1);emit('m6_output_submission',1018,epoch=2,nonzero=False,block_seconds=.02)
            emit('capture_callback',1020,capture_count=2);emit('response_task_cancelled',1021,generation=1)
            emit('m6_endpoint',1200,generation=2);emit('m6_output_submission',1500,epoch=2,nonzero=True,block_seconds=.02);emit('response_complete',1600,generation=2)
            emit('m6_trial_report',1700,outcome='pass',audible=True,response_correct=True,overlap=True,old_stopped=True,no_replay=True)
            (sub/'events.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in data),encoding='utf-8');result=analyze(folder);i=result['interruption_observations'][0]
            self.assertEqual(i['onset_to_cancellation_request_ms'],1);self.assertEqual(i['onset_to_queue_flush_ms'],3);self.assertEqual(i['onset_to_callback_epoch_stop_proxy_ms'],8)
            self.assertAlmostEqual(i['onset_to_estimated_old_pcm_drain_ms'],60);self.assertIsNone(i['physically_measured_onset_to_stop_ms']);self.assertTrue(i['capture_continues'])
            self.assertEqual(next(x for x in result['trials'] if x['trial_id']=='E01')['outcome'],'pass')
    def test_invalid_run_paths_and_budget_reservation_no_retry(self):
        for invalid in ('../run-1','run-20261020-001/x','x'):
            with self.assertRaises(ValueError):run_path(invalid)
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as temp:
            b=Budget(Path(temp)/'budget.json',{'asr':1});self.assertEqual(b.reserve('asr'),1)
            with self.assertRaisesRegex(ValueError,'budget_exhausted'):b.reserve('asr')
            self.assertEqual(read(b.path)['asr'],1)

class EventTests(unittest.TestCase):
    def test_cue_report_validation_and_generation_identity(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as temp:
            e=Events(Path(temp)/'A','A')
            try:
                e.command({'action':'cue','trial':'A01'});e.bind_generation(1);e.emit('m6_endpoint',generation=1)
                with self.assertRaises(ValueError):e.command({'action':'cue','trial':'A02'})
                with self.assertRaises(ValueError):e.command({'action':'report','trial':'A02','outcome':'pass'})
                e.command({'action':'report','trial':'A01','outcome':'fail','note':'wrong_answer','audible':True,'response_correct':False})
                with self.assertRaises(ValueError):e.command({'action':'cue','trial':'A01'})
                e.bind_generation(2);self.assertEqual(e.generations[2],'A-INC01')
            finally:e.close()
            data=[json.loads(s) for s in e.path.read_text().splitlines()];self.assertTrue(any(x['event']=='m6_incidental_trial' for x in data));self.assertTrue(all('session_id' in x and 'event_index' in x for x in data))

class ObserverTests(unittest.IsolatedAsyncioTestCase):
    async def test_observer_preserves_results_pcm_and_cancel_return(self):
        class State(Enum):QUIET=0;STARTING=1;SPEAKING=2;STOPPING=3
        sequence=iter([State.STARTING,State.SPEAKING,State.STOPPING,State.SPEAKING,State.QUIET]);inputs=[];record=[]
        async def original(data):inputs.append(data);return next(sequence)
        controller=SimpleNamespace(clock=SimpleNamespace(current=0))
        def interrupt():controller.clock.current+=1;return controller.clock.current
        controller.interrupt=interrupt
        processor=SimpleNamespace(vad=SimpleNamespace(analyze_audio=original),controller=controller)
        def callback(*args):return b'\x01\0',99
        output=SimpleNamespace(callback=callback);state=SimpleNamespace(last_callback_epoch=0,callback_count=1,rate=48000,capture_count=1,post=lambda event,**data:record.append((event,data)))
        transport=SimpleNamespace(output=lambda:output,state=state)
        e=SimpleNamespace(emit=lambda event,**data:record.append((event,data)),bind_generation=lambda g:None)
        attach(processor,transport,e);payload=b'unchanged'
        values=[await processor.vad.analyze_audio(payload) for _ in range(5)]
        self.assertEqual(values,[State.STARTING,State.SPEAKING,State.STOPPING,State.SPEAKING,State.QUIET]);self.assertEqual(inputs,[payload]*5)
        self.assertEqual(sum(x[0]=='m6_speech_onset_proxy' for x in record),1);self.assertEqual(sum(x[0]=='m6_endpoint' for x in record),1)
        self.assertEqual(controller.interrupt(),1);self.assertEqual(output.callback(None,960,{'current_time':1,'output_buffer_dac_time':1.1},0),(b'\x01\0',99))
        self.assertTrue(record[-1][1]['nonzero']);self.assertAlmostEqual(record[-1][1]['block_seconds'],.02)
