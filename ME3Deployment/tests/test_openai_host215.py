"""No credentials/network: inactive provider, explicit pins and goal/ordinal wiring."""
import ast,copy,importlib.util,json,os,sys,types,unittest
from pathlib import Path
from unittest.mock import patch
HERE=Path(__file__).resolve().parent;PACKET=HERE.parent;OVER=PACKET/'onit_candidate'
ROOT=Path(os.environ.get('ME3_PROJECT_ROOT','/opt/ME3Deployment'))
sys.path.insert(0,str(ROOT))

def load(name,path):
 spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
rp=load('host_profile215',OVER/'reasoning_profile.py')
with patch.dict(sys.modules,{'onit_candidate.reasoning_profile':rp}):engine=load('host_engine215',OVER/'engine.py')
catalog=load('host_catalog215',OVER/'catalog.py')
CandidateError=engine.CandidateError

def drive(coro):
 try:coro.send(None)
 except StopIteration as result:return result.value
 raise AssertionError('unexpected_external_IO')

class ProfileChecks(unittest.TestCase):
 def setUp(self):
  override=patch.object(rp,'ACTIVE','google_gemini');override.start();self.addCleanup(override.stop)
 def fake(self,selected=None):
  package=types.ModuleType('me3_openai');package.selected_profile=lambda root=None:copy.deepcopy(selected or dict(provider=rp.OPENAI_PROVIDER,model=rp.OPENAI_MODEL));package.Transport=lambda emit,**kwargs:types.SimpleNamespace(**kwargs);return package
 def test_google_profile_stays_active_and_cannot_import_OpenAI(self):
  self.assertEqual(rp.ACTIVE,'google_gemini')
  with patch.dict(sys.modules,{'me3_openai':None,'me3_openai.transport':None}):
   self.assertEqual(rp.profile(),{'provider':'google','model':'gemini-3.1-flash-lite'})
   actual=engine.Engine(ROOT,types.SimpleNamespace(),types.SimpleNamespace(model=rp.GEMINI_MODEL,provider='google'),None,None)
   self.assertFalse(actual.uses_lean_tools);self.assertEqual(actual.provider,'google')
  with self.assertRaises(CandidateError):engine.Engine(ROOT,types.SimpleNamespace(),types.SimpleNamespace(model=rp.OPENAI_MODEL,provider=rp.OPENAI_PROVIDER),None,None)
 def test_OpenAI_requires_active_exact_selected_model_provider_no_fallback(self):
  with patch.object(rp,'ACTIVE',rp.OPENAI_PROVIDER),patch.dict(sys.modules,{'me3_openai.transport':self.fake()}):
   self.assertEqual(rp.profile(ROOT),{'provider':rp.OPENAI_PROVIDER,'model':rp.OPENAI_MODEL})
   actual=engine.Engine(ROOT,types.SimpleNamespace(),types.SimpleNamespace(model=rp.OPENAI_MODEL,provider=rp.OPENAI_PROVIDER),None,None)
   self.assertTrue(actual.uses_lean_tools)
   self.assertEqual(rp.transport(lambda *a:None).model,rp.OPENAI_MODEL)
   for model,provider in ((rp.GEMINI_MODEL,'google'),(rp.OPENAI_MODEL,None),('other',rp.OPENAI_PROVIDER)):
    with self.assertRaises(CandidateError):engine.Engine(ROOT,types.SimpleNamespace(),types.SimpleNamespace(model=model,provider=provider),None,None)
  with patch.object(rp,'ACTIVE',rp.OPENAI_PROVIDER),patch.dict(sys.modules,{'me3_openai.transport':self.fake({'provider':rp.OPENAI_PROVIDER,'model':'other'})}):
   self.assertEqual(rp.profile(),{'provider':rp.OPENAI_PROVIDER,'model':rp.OPENAI_MODEL})
   with self.assertRaises(ValueError):rp.transport(lambda *a:None)
 def test_google_transport_factory_unchanged(self):
  fake=types.ModuleType('onit_candidate.gemini_native');seen=[]
  fake.Transport=lambda emit:seen.append(emit) or 'exact_google_transport'
  emit=lambda *a:None
  with patch.dict(sys.modules,{'onit_candidate.gemini_native':fake,'me3_openai':None,'me3_openai.transport':None}):self.assertEqual(rp.transport(emit),'exact_google_transport')
  self.assertEqual(seen,[emit])
 def test_OpenAI_capacity_is_local_keeps_ASR_guard_and_has_no_model_call(self):
  fake=self.fake();fake.capacity_snapshot=lambda root:dict(ready=True,reasons=[],refresh_required=True,network_calls=0,counters_changed=False)
  from onit_candidate import provider
  with patch.object(rp,'ACTIVE',rp.OPENAI_PROVIDER),patch.dict(sys.modules,{'me3_openai.transport':fake}),patch.object(provider,'capacity_snapshot',return_value=dict(ready=True,reasons=[])) as asr:
   result=rp.require_capacity(ROOT,'normal');self.assertTrue(result['ready']);self.assertTrue(result['refresh_required']);self.assertEqual(result['network_calls'],0);self.assertFalse(result['counters_changed']);asr.assert_called_once()
  fake.capacity_snapshot=lambda root:dict(ready=False,reasons=['account_unavailable'])
  with patch.object(rp,'ACTIVE',rp.OPENAI_PROVIDER),patch.dict(sys.modules,{'me3_openai.transport':fake}):
   with self.assertRaises(ValueError):rp.require_capacity(ROOT,'text')

class HookChecks(unittest.TestCase):
 def test_goal_once_then_exact_ordinals_without_counter_mutation(self):
  seen=[];transport=types.SimpleNamespace(begin_goal=lambda goal:seen.append(('begin',goal)),bind_reservation=lambda ordinal:seen.append(('bind',ordinal)),end_goal=lambda:seen.append(('end',)))
  engine.begin_reasoning_goal(transport,rp.OPENAI_PROVIDER,'actual-goal')
  engine.bind_reasoning_reservation(transport,rp.OPENAI_PROVIDER,1075);engine.bind_reasoning_reservation(transport,rp.OPENAI_PROVIDER,1076)
  self.assertEqual(seen,[('begin','actual-goal'),('bind',1075),('bind',1076)])
  engine.begin_reasoning_goal(transport,'google','other');engine.bind_reasoning_reservation(transport,'google',1077);self.assertEqual(len(seen),3)
  engine.end_reasoning_goal(transport,'google');self.assertEqual(len(seen),3)
  engine.end_reasoning_goal(transport,rp.OPENAI_PROVIDER);self.assertEqual(seen[-1],('end',))
  with self.assertRaises(CandidateError):engine.begin_reasoning_goal(types.SimpleNamespace(),rp.OPENAI_PROVIDER,'goal')
  with self.assertRaises(CandidateError):engine.bind_reasoning_reservation(types.SimpleNamespace(),rp.OPENAI_PROVIDER,1078)
  with self.assertRaises(CandidateError):engine.end_reasoning_goal(types.SimpleNamespace(),rp.OPENAI_PROVIDER)
 def test_actual_Engine_binds_goal_outside_loop_and_ordinal_after_reserve_before_request(self):
  tree=ast.parse((OVER/'engine.py').read_text());answer=next(node for node in ast.walk(tree) if isinstance(node,ast.AsyncFunctionDef) and node.name=='answer');calls=[node for node in ast.walk(answer) if isinstance(node,ast.Call)]
  begin=[node for node in calls if isinstance(node.func,ast.Name) and node.func.id=='begin_reasoning_goal'];self.assertEqual(len(begin),1)
  for loop in (node for node in ast.walk(answer) if isinstance(node,(ast.While,ast.For))):self.assertNotIn(begin[0],list(ast.walk(loop)))
  reserve=next((node.lineno,node.col_offset) for node in calls if isinstance(node.func,ast.Attribute) and node.func.attr=='reserve')
  binding=next((node.lineno,node.col_offset) for node in calls if isinstance(node.func,ast.Name) and node.func.id=='bind_reasoning_reservation')
  request=next((node.lineno,node.col_offset) for node in calls if isinstance(node.func,ast.Attribute) and node.func.attr=='request')
  self.assertLess(reserve,binding);self.assertLess(binding,request)
  self.assertIn('reasoning_provider=self.provider',(OVER/'engine.py').read_text())
 def test_provider_lean_presentation_preserves70_scope_and_GUIuses_Engine_policy(self):
  path=PACKET/'tests/fixtures/openai215-tools.json';all_declarations=json.loads(path.read_bytes())['normal_retained_declarations'];eager=catalog.eager_schema_names(all_declarations)
  lean=catalog.Catalog(all_declarations,eager=eager);self.assertEqual(len(lean.all),70);self.assertEqual(len(lean.declarations()),20)
  self.assertEqual(set(lean.all),{d['name'] for d in all_declarations});before=copy.deepcopy(all_declarations);lean.declarations()[0]['parameters'].clear();self.assertEqual(before,all_declarations)
  direct=catalog.Catalog(all_declarations,eager={d['name'] for d in all_declarations});self.assertEqual(len(direct.declarations()),71)
  self.assertIn("lazy_tools=getattr(self.engine,'uses_lean_tools',False)",(OVER/'conversation.py').read_text());self.assertIn("if self.uses_lean_tools:eager=eager_schema_names(declarations)",(OVER/'engine.py').read_text())

class ChildChecks(unittest.TestCase):
 def setUp(self):
  tree=ast.parse((OVER/'child.py').read_text());wanted={'StopCandidate','validate_child_model','OutputVisibility','broker_chat'};nodes=[node for node in tree.body if isinstance(node,(ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef)) and node.name in wanted]
  self.calls=[]
  async def chat(**kwargs):self.calls.append(kwargs);return 'complete-original-loop-result'
  def original(total,*texts):return total-sum(len(text or '') for text in texts)//3>=512
  self.c=types.SimpleNamespace(chat=chat,_output_unaccounted=original);self.original=original
  scope=dict(c=self.c,MODEL=rp.GROQ_MODEL,GEMINI_MODEL=rp.GEMINI_MODEL,OPENAI_MODEL=rp.OPENAI_MODEL,OPENAI_PROVIDER=rp.OPENAI_PROVIDER)
  exec(compile(ast.Module(body=nodes,type_ignores=[]),'staged-child-pure','exec'),scope);self.s=types.SimpleNamespace(**scope)
 def test_explicit_child_pin_not_global_model_spoof(self):
  self.s.validate_child_model(rp.GEMINI_MODEL,'google');self.s.validate_child_model(rp.GROQ_MODEL,'legacy');self.s.validate_child_model(rp.OPENAI_MODEL,rp.OPENAI_PROVIDER)
  for model,provider in ((rp.OPENAI_MODEL,'google'),(rp.GEMINI_MODEL,rp.OPENAI_PROVIDER),(rp.OPENAI_MODEL,'unknown')):
   with self.assertRaises(self.s.StopCandidate):self.s.validate_child_model(model,provider)
 def test_visibility_excludes_reasoning_only_no_response_or_usage_rewrite(self):
  value=dict(model=rp.OPENAI_MODEL,usage=dict(completion_tokens=900,completion_tokens_details=dict(reasoning_tokens=800)));old=copy.deepcopy(value);guard=self.s.OutputVisibility(self.original)
  guard.observe(value);self.assertFalse(guard.check(900,'Short answer.'));self.assertEqual(value,old)
  value['usage']['completion_tokens_details']['reasoning_tokens']=0;guard.observe(value);self.assertTrue(guard.check(900,'Short answer.'))
  value['usage']['completion_tokens_details']['reasoning_tokens']=901
  with self.assertRaises(self.s.StopCandidate):guard.observe(value)
  with self.assertRaises(self.s.StopCandidate):guard.check(900,'Short answer.')
 def test_private_broker_only_OpenAI_and_original_guard_restored(self):
  kwargs=dict(host='https://api.openai.com/v1',host_key='BROKER_OWNS_CREDENTIALS',model=rp.OPENAI_MODEL,instruction='Unchanged request.');guard=self.s.OutputVisibility(self.original)
  result=drive(self.s.broker_chat(rp.OPENAI_PROVIDER,guard,**kwargs));self.assertEqual(result,'complete-original-loop-result');self.assertEqual(self.calls,[dict(kwargs,host='https://me3-broker.invalid/v1')]);self.assertIs(self.c._output_unaccounted,self.original)
  self.calls.clear();drive(self.s.broker_chat('google',None,**dict(kwargs,model=rp.GEMINI_MODEL)));self.assertEqual(self.calls,[dict(kwargs,model=rp.GEMINI_MODEL)])
  with self.assertRaises(self.s.StopCandidate):drive(self.s.broker_chat(rp.OPENAI_PROVIDER,guard,**dict(kwargs,host='https://unexpected.invalid')))

if __name__=='__main__':unittest.main(verbosity=2)
