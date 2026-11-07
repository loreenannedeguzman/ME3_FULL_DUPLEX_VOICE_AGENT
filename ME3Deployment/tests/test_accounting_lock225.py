"""Real local accounting-lock contention; synthetic budget, no providers."""
import ast,hashlib,json,os,subprocess,sys,tempfile,threading,time,types,unittest
from pathlib import Path
from unittest.mock import patch
from me3_assistant import guard,operating
from onit_candidate.conversation import Conversation
from onit_candidate.engine import host_failure_code
from onit_candidate import engine as engine_module
ROOT=Path(os.environ.get('ME3_PROJECT_ROOT',Path(__file__).resolve().parents[1]))
def raw(value):return (json.dumps(value,indent=2)+'\n').encode()
def save(path,value):path.write_bytes(raw(value))
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
CHILD="""import importlib.util,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('held_guard225',sys.argv[1]);g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)
with g.lock(Path(sys.argv[2])):
 print('HELD',flush=True)
 sys.stdin.readline()
"""
class AccountingLockChecks(unittest.TestCase):
 def setUp(self):
  (ROOT/'.tmp').mkdir(exist_ok=True);self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='accounting225-');self.path=Path(self.tmp.name);self.phase=self.path/'phase';self.phase.mkdir();self.state=self.path/'state.json';self.policy=self.path/'policy.json';self.resource=self.path/'resource.json';save(self.policy,{'synthetic_authority':True});save(self.resource,{'synthetic_policy':True});self.initial=dict(asr=5,reasoning=8,tts=13,commit_item=0);save(self.phase/'budget.json',self.initial);self.prefix=[90.0,95.0];save(self.state,dict(policy_sha256=digest(self.policy),baseline={k:0 for k in self.initial},usage=self.initial,pending=None,hosted_attempts=self.prefix));self.grant=dict(policy_path=str(self.policy),policy_sha256=digest(self.policy),resource_policy_path=str(self.resource),resource_policy_sha256=digest(self.resource),operation_state_path=str(self.state),mode='normal');self.budget=self.new_budget();self.children=[];self.timers=[]
 def tearDown(self):
  for timer in self.timers:timer.cancel();timer.join()
  for child in self.children:
   if child.poll() is None:
    try:child.stdin.write('\n');child.stdin.flush();child.wait(timeout=5)
    except (OSError,subprocess.TimeoutExpired):child.kill();child.wait(timeout=5)
   child.stdin.close();child.stdout.close();child.stderr.close()
  self.tmp.cleanup()
 def new_budget(self):return operating.OperatingBudget(dict(self.grant),{},self.phase,clock=lambda:100.0)
 def held(self,path):
  env={**os.environ,'PYTHONPATH':os.pathsep.join((str(ROOT),str(ROOT/'tools'))),'PYTHONDONTWRITEBYTECODE':'1'}
  child=subprocess.Popen([sys.executable,'-P','-B','-c',CHILD,str(Path(guard.__file__).resolve()),str(path)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env,cwd=ROOT);self.children.append(child);self.assertEqual(child.stdout.readline().strip(),'HELD');return child
 def release_later(self,child,seconds=.06):
  def release():child.stdin.write('\n');child.stdin.flush()
  timer=threading.Timer(seconds,release);self.timers.append(timer);timer.start()
 def snapshots(self):return {p.name:p.read_bytes() for p in (self.policy,self.resource,self.state,self.phase/'budget.json')}
 def before_reasoning(self):
  owner=types.SimpleNamespace(current=lambda generation:True,active_generation=1,voice=True,engine=types.SimpleNamespace(budget=self.budget));Conversation.before_reasoning(owner)
 def test_original_voice_failfast_reproduces_without_a_model_request(self):
  self.held(self.phase/'normal-reservation.lock');before=self.snapshots();original=guard.lock
  with patch.object(guard,'lock',side_effect=lambda path,**kwargs:original(path)):
   with self.assertRaisesRegex(ValueError,'assistant_resource_lease_busy'):self.before_reasoning()
  self.assertEqual(self.snapshots(),before)
 def test_voice_allowance_waits_then_reserves_exactly_once(self):
  holder=self.held(self.phase/'normal-reservation.lock');self.release_later(holder);start=time.monotonic();self.before_reasoning();elapsed=time.monotonic()-start;ordinal=self.budget.reserve('reasoning');state=json.loads(self.state.read_bytes());after=json.loads((self.phase/'budget.json').read_bytes())
  self.assertGreaterEqual(elapsed,.03);self.assertLess(elapsed,operating.ACCOUNTING_LOCK_TIMEOUT+.5);self.assertEqual(ordinal,9);self.assertEqual(after,{**self.initial,'reasoning':9});self.assertEqual(state['usage'],after);self.assertIsNone(state['pending']);self.assertEqual(state['hosted_attempts'],self.prefix+[100.0]);self.assertEqual(digest(self.policy),self.grant['policy_sha256']);self.assertEqual(digest(self.resource),self.grant['resource_policy_sha256'])
 def test_accounting_timeout_is_bounded_and_does_not_charge(self):
  self.held(self.phase/'normal-reservation.lock');before=self.snapshots();start=time.monotonic()
  with patch.object(operating,'ACCOUNTING_LOCK_TIMEOUT',.05):
   with self.assertRaisesRegex(ValueError,'assistant_resource_lease_busy'):self.budget.reserve('reasoning')
  elapsed=time.monotonic()-start;self.assertGreaterEqual(elapsed,.04);self.assertLess(elapsed,.5);self.assertEqual(self.snapshots(),before)
 def test_session_and_source_ownership_stay_failfast(self):
  for name in ('session.lock','source.lock'):
   path=self.path/name;self.held(path);start=time.monotonic()
   with self.assertRaisesRegex(ValueError,'assistant_resource_lease_busy'):
    with guard.lock(path):self.fail('contended ownership lock acquired')
   self.assertLess(time.monotonic()-start,.2)
 def test_status_readers_and_single_writer_preserve_counter_integrity(self):
  holder=self.held(self.phase/'normal-reservation.lock');barrier=threading.Barrier(3);errors=[];observed=[]
  def reader():
   try:
    budget=self.new_budget();barrier.wait(timeout=2)
    for i in range(8):observed.append(budget.remaining('reasoning'))
   except BaseException as exc:errors.append(type(exc).__name__)
  readers=[threading.Thread(target=reader) for i in range(2)]
  for thread in readers:thread.start()
  barrier.wait(timeout=2);self.release_later(holder);self.assertEqual(self.budget.reserve('reasoning'),9)
  for thread in readers:thread.join(timeout=3);self.assertFalse(thread.is_alive())
  self.assertEqual(errors,[]);self.assertEqual(len(observed),16);self.assertTrue(all(v in {11,12} for v in observed));self.assertEqual(json.loads((self.phase/'budget.json').read_bytes()),{**self.initial,'reasoning':9});self.assertEqual(json.loads(self.state.read_bytes())['hosted_attempts'],self.prefix+[100.0])
 def test_hash_and_counter_failures_are_not_retried_or_repaired(self):
  baseline=self.snapshots();save(self.resource,{'different':True})
  with self.assertRaisesRegex(ValueError,'operating_policy_changed'):self.budget.remaining('reasoning')
  self.resource.write_bytes(baseline[self.resource.name]);save(self.phase/'budget.json',{**self.initial,'reasoning':7});before=self.snapshots()
  with self.assertRaisesRegex(ValueError,'operating_counter_decreased_or_changed'):self.budget.reserve('reasoning')
  self.assertEqual(self.snapshots(),before)
 def test_fixed_diagnostics_reveal_only_allowlisted_host_codes(self):
  self.assertEqual(host_failure_code(ValueError('assistant_resource_lease_busy')),'assistant_resource_lease_busy')
  for exc in (ValueError('PRIVATE_OWNER_TEXT'),ValueError('assistant_resource_lease_busy','PRIVATE'),TypeError('assistant_resource_lease_busy')):self.assertIsNone(host_failure_code(exc))
  tree=ast.parse(Path(engine_module.__file__).read_bytes());self.assertTrue(any(isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='host_failure_code' for n in ast.walk(tree)))
if __name__=='__main__':unittest.main(verbosity=2)
