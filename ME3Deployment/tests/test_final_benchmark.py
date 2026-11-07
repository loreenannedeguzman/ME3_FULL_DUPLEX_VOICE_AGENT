"""Provider-free benchmark contracts/observer fixtures; no live acoustic evidence."""
import asyncio,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from benchmark_final.protocol import SESSIONS,TRIALS,LIMITS
from benchmark_m6.protocol import SESSIONS as HISTORICAL
from benchmark_final.analyze import analyze_data,js_divergence,stats,stages
from benchmark_final.console import authorization,Controller,verify_environment
from benchmark_final.observe import attach
class MetricTests(unittest.TestCase):
    def row(self,event,t,**kw):return {'event':event,'monotonic_ns':int(t*1e9),'trial_id':'A01',**kw}
    def fixture(self):
        r=self.row
        return [r('m6_trial_start',0),r('bf_speech_onset',1),r('bf_endpoint',2),r('core_turn_started',2.1,generation=1),r('mcp_request_start',3,role='asr',generation=1,request_id='a'),r('mcp_request_end',4,role='asr',generation=1,request_id='a'),r('m6_reasoning_request_start',4,generation=1),r('m6_reasoning_request_end',5,generation=1),r('mcp_request_start',5,role='tts',generation=1,request_id='t'),r('mcp_request_end',6,role='tts',generation=1,request_id='t'),r('bf_audio_submission',6.5,generation=1,kind='response',nonzero=True),r('m6_trial_report',8,outcome='pass',audible=True,response_correct=True)]
    def test_m6_normal_scripts_unchanged_and_task_is_nondestructive(self):
        self.assertEqual(len(TRIALS),31);self.assertEqual(sum(x['kind']=='normal' for x in TRIALS.values()),20)
        for s in 'ABCD':self.assertEqual(SESSIONS[s],HISTORICAL[s])
        self.assertIn('six rulers',SESSIONS['F'][-1]['script'][0]);self.assertIn('five pencils',HISTORICAL['F'][-1]['script'][0])
    def test_latencies_reproduce_and_acoustic_references_not_invented(self):
        out=analyze_data({'A01':{'kind':'normal'}},self.fixture());trial=out['trials'][0]
        self.assertEqual(trial['state'],'pass');self.assertEqual(trial['endpoint_to_substantive_submission_seconds'],4.5);self.assertEqual(trial['selected_ASR_seconds'],1);self.assertEqual(trial['reasoning_seconds'],1);self.assertEqual(trial['TTS_seconds'],1)
        self.assertEqual(out['physical_acoustic_onset_stop'],'NOT_MEASURED');self.assertEqual(out['backchannels']['JSD']['status'],'NOT_MEASURED');self.assertEqual(out['GPT4o_quality']['status'],'NOT_MEASURED')
    def test_failed_semantic_audio_stays_in_latency_population(self):
        data=self.fixture();data[-1].update(outcome='fail',response_correct=False);out=analyze_data({'A01':{'kind':'normal'}},data)
        self.assertEqual(out['accounting']['outcomes'],{'fail':1});self.assertEqual(out['normal_latency']['endpoint_to_submission']['n'],1)
    def test_timeout_censored_not_silently_excluded_or_promoted_by_later_report(self):
        r=self.row;data=self.fixture();data[5]=r('mcp_request_error',4,role='asr',generation=1,request_id='a',error_type='TimeoutError')
        out=analyze_data({'A01':{'kind':'normal'}},data);self.assertEqual(out['trials'][0]['state'],'fail');self.assertIsNone(out['trials'][0]['selected_ASR_seconds']);self.assertEqual(out['accounting']['timeout_trials'],1)
    def test_missing_and_unstarted_are_separate(self):
        out=analyze_data({'A01':{'kind':'normal'},'A02':{'kind':'normal'}},[self.row('m6_trial_start',0)])
        self.assertEqual(out['accounting']['outcomes'],{'incomplete':1,'unstarted':1})
    def test_backchannel_audio_is_not_substituted_for_substantive_answer(self):
        data=self.fixture();data.insert(4,self.row('bf_audio_submission',2.5,generation=1,kind='backchannel',nonzero=True));out=analyze_data({'A01':{'kind':'normal'}},data);self.assertEqual(out['trials'][0]['endpoint_to_substantive_submission_seconds'],4.5)
    def test_duplicate_operator_report_rejected(self):
        data=self.fixture();data.append(data[-1])
        with self.assertRaises(ValueError):analyze_data({'A01':{'kind':'normal'}},data)
    def test_cancelled_and_incomplete_request_durations_remain_censored(self):
        r=self.row;data=[r('mcp_request_start',1,role='asr',generation=1,request_id='a'),r('mcp_request_cancelled',2,request_id='a'),r('mcp_request_start',3,role='tts',generation=1,request_id='t')];out=stages(data);self.assertEqual([x['state'] for x in out],['canceled','incomplete']);self.assertIsNone(out[0]['complete_seconds']);self.assertEqual(out[0]['elapsed_to_terminal_seconds'],1)
    def test_p95_only_when_twenty_available_and_no_threshold_claim(self):
        self.assertIsNone(stats(list(range(19)))['p95_seconds']);self.assertEqual(stats(list(range(20)))['p95_seconds'],18)
    def test_reference_jsd_requires_real_aligned_inputs_not_missing_zero(self):
        self.assertAlmostEqual(js_divergence([1,0],[1,0]),0);self.assertAlmostEqual(js_divergence([1,0],[0,1]),1)
        for p,q in [([],[]),([0],[1]),([-1,2],[1,2]),([float('nan')],[1])]:
            with self.assertRaises(ValueError):js_divergence(p,q)
class GuardTests(unittest.TestCase):
    def test_fresh_exact_allowance_required(self):
        with self.assertRaises(ValueError):authorization('../outside')
        with tempfile.TemporaryDirectory() as tmp,patch('benchmark_final.console.PHASE',Path(tmp)):
            path=Path(tmp)/'authorization-run-20261020-001.json';path.write_text(json.dumps({'approved':True,'run_id':'run-20261020-001','limits':LIMITS,'existing_google_allowance_confirmed':False}))
            with self.assertRaises(ValueError):authorization('run-20261020-001')
            data=json.loads(path.read_text());data['existing_google_allowance_confirmed']=True;path.write_text(json.dumps(data));self.assertEqual(authorization('run-20261020-001')['limits'],LIMITS)
    def test_historical_delete_action_is_unavailable(self):
        c=Controller('run-20261020-001')
        with self.assertRaisesRegex(ValueError,'no_deletions'):c.action({'action':'prepare_task'})
class ObserverTests(unittest.IsolatedAsyncioTestCase):
    async def test_observer_does_not_guess_generation_advance_or_change_pcm(self):
        events=[];frame=b'\1\0';result=(frame,0)
        class E:
            active='A01'
            def emit(self,event,**kw):events.append((event,kw))
        async def vad(pcm):self.assertEqual(pcm,frame);return SimpleNamespace(name='SPEAKING')
        state=SimpleNamespace(last_callback_epoch=2,buffer=SimpleNamespace(epoch=2),callback_count=1,capture_count=7,rate=48000)
        state.post=lambda event,**kw:events.append((event,kw))
        output=SimpleNamespace(callback=lambda *a:result)
        processor=SimpleNamespace(vad=SimpleNamespace(analyze_audio=vad),token=1,output_generation=5,output_kind='response',controller=SimpleNamespace(clock=SimpleNamespace(current=5)))
        transport=SimpleNamespace(state=state,output=lambda:output)
        attach(processor,transport,E());await processor.vad.analyze_audio(frame);self.assertEqual(output.callback(None,960,{},0),result)
        onset=next(x for name,x in events if name=='bf_speech_onset');self.assertNotIn('generation',onset);self.assertEqual(onset['token'],2);sample=next(x for name,x in events if name=='bf_audio_submission');self.assertEqual(sample['generation'],5);self.assertEqual(sample['kind'],'response')

class IdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_probe_request_keeps_original_trial_after_active_trial_changes(self):
        from benchmark_final.runner import Events
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)/'logs';e=Events(folder,'A')
            try:
                e.active='A01';e.emit('core_turn_started',generation=5)
                e.emit('mcp_request_start',generation=6,request_id='probe',role='asr');e.active='A02'
                e.emit('mcp_request_cancelled',generation=6,request_id='probe',role='asr')
                self.assertNotIn(6,e.generations);self.assertEqual(e.current_generation,5)
            finally:e.close()
            data=[json.loads(x) for x in (folder/'events.jsonl').read_text().splitlines()];self.assertEqual(data[-1]['trial_id'],'A01')
    async def test_direct_runner_cannot_bypass_fresh_allowance_or_open_audio(self):
        from benchmark_final.runner import run
        with patch('benchmark_final.console.verify_sources',return_value={}),patch('benchmark_final.console.authorization',side_effect=ValueError('fresh_allowance_required')),patch('benchmark_final.runner.Settings.load') as settings:
            with self.assertRaisesRegex(ValueError,'fresh_allowance'):await run('run-20261020-001','A')
            settings.assert_not_called()
    async def test_changed_public_model_configuration_is_rejected(self):
        expected={'asr_model':'approved-model','text_model':'approved-model'};verify_environment(expected,dict(expected))
        with self.assertRaises(ValueError):verify_environment({'asr_model':'different','text_model':'approved-model'},expected)

class CoverageTests(unittest.TestCase):
    row=MetricTests.row
    fixture=MetricTests.fixture
    def test_startup_is_separate_from_normal_latency(self):
        data=self.fixture()+[{'event':'mcp_request_start','monotonic_ns':0,'trial_id':None,'role':'tts','generation':0,'request_id':'startup'},{'event':'mcp_request_end','monotonic_ns':500000000,'trial_id':None,'role':'tts','generation':0,'request_id':'startup'}]
        out=analyze_data({'A01':{'kind':'normal'}},data)
        self.assertEqual(out['startup_requests']['TTS']['n'],1);self.assertEqual(out['normal_latency']['TTS']['n'],1)
    def test_interruption_takeover_requires_observed_overlap_and_replacement(self):
        data=self.fixture();data[1]['output_kind']='response';data[-1].update(overlap=True,old_stopped=True,no_replay=True)
        out=analyze_data({'A01':{'kind':'interrupt'}},data)
        self.assertEqual(out['interruption_summary']['takeover_rate_of_started'],1);self.assertEqual(out['interruption_summary']['latency_only_TO1']['n'],1)
        data[1].pop('output_kind');out=analyze_data({'A01':{'kind':'interrupt'}},data)
        self.assertEqual(out['interruption_summary']['takeover_rate_of_started'],0);self.assertEqual(out['interruption_summary']['latency_only_TO1']['n'],0)
