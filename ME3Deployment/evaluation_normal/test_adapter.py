"""Provider-free tests; actual guard delegation is tested, no synthetic live pass."""
import asyncio,hashlib,importlib.util,io,json,sys,tempfile,types,unittest
from contextlib import redirect_stderr
from unittest.mock import patch
from datetime import datetime,timedelta,timezone
from pathlib import Path
spec=importlib.util.spec_from_file_location('normal_benchmark_adapter',Path(__file__).with_name('adapter.py'));a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
class Sink:
    current_generation=7
    active=None
    def __init__(self):self.rows=[]
    def emit(self,event,**fields):self.rows.append({'event':event,**fields})
class AdapterTests(unittest.TestCase):
    def test_protocol_preserves31identities_and_uses_seven_row_readonly_task(self):
        plan,methods=a.protocol();self.assertEqual(sum(map(len,plan.values())),31)
        self.assertIn('seven',plan['F'][-1]['expected']);self.assertIn('No proposal or write',plan['F'][-1]['expected'])
        self.assertIn('NOT_MEASURED',methods['JSD']);self.assertIn('normal Router',methods['reasoning'])
    def test_missing_manifest_blocks_before_runtime_construction(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=Path(tmp);base=r/'evidence/benchmark_normal'
            with self.assertRaises(FileNotFoundError):a.verify_run(r,base/'run-001','A',base/'scope',base/'grant.json')
            self.assertFalse(base.exists())
    def test_paths_cannot_select_development_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):a.project_path(Path(tmp),Path(tmp)/'evidence/completion/dev')
    def test_event_projection_and_real_request_aliases(self):
        sink=Sink();bridge=a.EventBridge(sink)
        bridge('assistant_rpc_start',request_id='r',generation=7,tool='list_items',arguments={'secret':'x'},text='private')
        self.assertEqual([x['event'] for x in sink.rows],['assistant_rpc_start','task_mcp_request_start'])
        self.assertTrue(all('arguments' not in x and 'text' not in x for x in sink.rows))
    def test_per_step_error_and_cancellation_are_not_completed_requests(self):
        async def scenario(error):
            class Model:
                async def decide(self,*args):raise error
            sink=Sink();module=types.SimpleNamespace(authorize=lambda *args,**kw:None,SharedBudget=object,observe_pipeline=lambda *args:None,Reasoning=Model)
            with a.bind_runtime(module,'unused','unused',sink,lambda *args:None):
                with self.assertRaises(type(error)):await module.Reasoning().decide([])
            return [r['event'] for r in sink.rows]
        self.assertEqual(asyncio.run(scenario(ValueError('fixture'))),['ba_llm_start','ba_llm_error'])
        self.assertEqual(asyncio.run(scenario(asyncio.CancelledError())),['ba_llm_start','ba_llm_cancelled'])
    def test_scoped_binding_delegates_to_original_guard_budget_and_observer(self):
        with tempfile.TemporaryDirectory() as tmp:
            phase=Path(tmp);(phase/'authorization.json').write_text(json.dumps({'limits':{'asr':48,'reasoning':60,'tts':64,'commit_item':0}}));calls=[]
            def auth(config,source,**kwargs):calls.append(('guard',source,kwargs));return {'allowed_actions':[]},source['budget_limits']
            def budget(grant,limits,**kw):calls.append(('budget',kw));return 'original-budget'
            def observed(*args):calls.append(('observer',args))
            class Model:
                async def decide(self,*args):return 'original-result'
            module=types.SimpleNamespace(authorize=auth,SharedBudget=budget,observe_pipeline=observed,Reasoning=Model)
            sink=Sink();attach=lambda *args:calls.append(('attach',args));source={'revision':'qualified','budget_limits':{'asr':0}}
            with a.bind_runtime(module,phase,phase/'grant.json',sink,attach):
                grant,limits=module.authorize({},source);self.assertEqual(module.SharedBudget(grant,limits),'original-budget')
                module.observe_pipeline((None,None,'transport','processor'),'settings','normal-events')
                self.assertEqual(asyncio.run(module.Reasoning().decide([])),'original-result')
            self.assertIs(module.authorize,auth);self.assertIs(module.SharedBudget,budget);self.assertIs(module.Reasoning,Model);self.assertIs(module.observe_pipeline,observed)
            self.assertEqual(source['budget_limits'],{'asr':0});self.assertEqual(calls[0][2],{'grant_path':phase/'grant.json','phase':phase})
            self.assertEqual(calls[1][1],{'phase':phase});self.assertEqual([x[0] for x in calls[2:]],['observer','attach'])
    def test_bindings_restored_on_failure(self):
        module=types.SimpleNamespace(authorize=object(),SharedBudget=object(),observe_pipeline=object(),Reasoning=object);old=module.__dict__.copy()
        with self.assertRaises(RuntimeError):
            with a.bind_runtime(module,'p','g',Sink(),None):raise RuntimeError('fixture')
        self.assertEqual(module.__dict__,old)
    def test_readonly_binding_rejects_actions_even_if_underlying_guard_returns_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            phase=Path(tmp);(phase/'authorization.json').write_text(json.dumps({'limits':{'commit_item':0}}))
            module=types.SimpleNamespace(authorize=lambda *args,**kw:({'allowed_actions':['write']},{'commit_item':0}),SharedBudget=object,observe_pipeline=object(),Reasoning=object)
            with a.bind_runtime(module,phase,phase/'grant.json',Sink(),None):
                with self.assertRaisesRegex(ValueError,'readonly'):module.authorize({}, {})
    def test_actual_guard_and_counter_enforcement_in_isolated_fixture(self):
        from me3_assistant.guard import authorize,SharedBudget
        from speech_mcp.client import ProviderLimited
        with tempfile.TemporaryDirectory() as tmp:
            phase=Path(tmp);limits={'asr':4,'reasoning':5,'tts':10,'commit_item':0};start={k:0 for k in limits};cfg={'sha256':'fixture','runtime':{'max_turns':8},'approved_modules':[],'actions':[]}
            authority=phase/'authorization.json';authority.write_text(json.dumps({'approved':True,'limits':limits}));(phase/'budget.json').write_text(json.dumps(start))
            now=datetime.now(timezone.utc);grant={'approved':True,'mode':'normal','provider_allowance_confirmed':True,'operator_ready':True,'source_revision':'fixture-source','config_sha256':'fixture','runtime':cfg['runtime'],'budget_authorization_sha256':hashlib.sha256(authority.read_bytes()).hexdigest(),'issued_at_utc':now.isoformat(),'expires_at_utc':(now+timedelta(minutes=10)).isoformat(),'budget_start':start,'max_reservations':limits,'allowed_actions':[]};path=phase/'grant.json';path.write_text(json.dumps(grant))
            module=types.SimpleNamespace(authorize=authorize,SharedBudget=SharedBudget,observe_pipeline=lambda *args:None,Reasoning=object)
            with a.bind_runtime(module,phase,path,Sink(),None):
                g,l=module.authorize(cfg,{'revision':'fixture-source','budget_limits':{'not_benchmark':'unchanged'}});b=module.SharedBudget(g,l)
                self.assertEqual(b.reserve('asr'),1);self.assertEqual(a.read(phase/'budget.json')['asr'],1)
                with self.assertRaises(ProviderLimited):b.reserve('commit_item')
                grant['expires_at_utc']=(now-timedelta(seconds=1)).isoformat();path.write_text(json.dumps(grant))
                with self.assertRaisesRegex(ValueError,'fresh_normal'):module.authorize(cfg,{'revision':'fixture-source'})
    def test_terminal_failure_does_not_render_provider_exception_payload(self):
        async def failure(*args):raise ValueError('PRIVATE_PROVIDER_PAYLOAD_DO_NOT_LOG')
        out=io.StringIO()
        with patch.object(a,'run',failure),patch.object(sys,'argv',['adapter','--run','r','--session','A','--scope','s','--grant','g']),redirect_stderr(out):
            self.assertEqual(a.main(),2)
        self.assertNotIn('PRIVATE_PROVIDER_PAYLOAD_DO_NOT_LOG',out.getvalue());self.assertEqual(json.loads(out.getvalue()),{'status':'FAIL','error_type':'ValueError','payload_omitted':True})
    def test_prior_benchmark_history_chain_retains_original_and_last_opening(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);old=root/'evidence/benchmark_agent/run-20261021-001/budget.json';prior=root/'evidence/benchmark_normal/accounting-prior/budget.json';phase=root/'evidence/benchmark_normal/accounting-next'
            def record(p,use):
                p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(use));return {'path':p.relative_to(root).as_posix(),'sha256':a.sha(p),'usage':use}
            first=record(old,{'asr':35,'reasoning':44,'tts':45,'commit_item':0});last=record(prior,{'asr':37,'reasoning':47,'tts':49,'commit_item':0})
            self.assertEqual(a.verify_history(root,phase,{'historical_benchmark_baseline':first}),first)
            authority={'historical_benchmark_baseline':last,'historical_benchmark_chain':[first,last]};self.assertEqual(a.verify_history(root,phase,authority),last)
            self.assertEqual(a.read(old),first['usage']);self.assertEqual(a.read(prior),last['usage']);self.assertFalse(phase.exists())
    def test_prior_benchmark_history_rejects_tamper_self_reference_and_regression(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);old=root/'evidence/benchmark_agent/run-20261021-001/budget.json';prior=root/'evidence/benchmark_normal/accounting-prior/budget.json';old.parent.mkdir(parents=True);prior.parent.mkdir(parents=True)
            old.write_text(json.dumps({'asr':35,'reasoning':44,'tts':45,'commit_item':0}));prior.write_text(json.dumps({'asr':37,'reasoning':47,'tts':49,'commit_item':0}))
            first={'path':old.relative_to(root).as_posix(),'sha256':a.sha(old),'usage':a.read(old)};last={'path':prior.relative_to(root).as_posix(),'sha256':a.sha(prior),'usage':a.read(prior)};authority={'historical_benchmark_baseline':last,'historical_benchmark_chain':[first,last]}
            with self.assertRaisesRegex(ValueError,'immutable_previous_scope'):a.verify_history(root,prior.parent,authority)
            changed=dict(last,sha256='tampered')
            with self.assertRaisesRegex(ValueError,'counter_changed'):a.verify_history(root,root/'next',{'historical_benchmark_baseline':changed,'historical_benchmark_chain':[first,changed]})
            prior.write_text(json.dumps({'asr':34,'reasoning':47,'tts':49,'commit_item':0}));lower=dict(last,sha256=a.sha(prior),usage=a.read(prior))
            with self.assertRaisesRegex(ValueError,'monotonic'):a.verify_history(root,root/'next',{'historical_benchmark_baseline':lower,'historical_benchmark_chain':[first,lower]})
            outside=dict(last,path='../outside/budget.json')
            with self.assertRaises(ValueError):a.verify_history(root,root/'next',{'historical_benchmark_baseline':outside,'historical_benchmark_chain':[first,outside]})
if __name__=='__main__':unittest.main()
