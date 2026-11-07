"""WB-01.3 fake-clock/quota/HTTP tests. No keys, external API or audio devices."""
import asyncio,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from me3_assistant.admission import Admission,AdmissionDenied,duration,estimated_tokens
from me3_assistant import groq_backend as g
from me3_assistant.groq_json import JSONGroqReasoning
from tests import test_assistant_groq_adapter as f
from tests import test_assistant_mode as m
from task_mcp.client import ROOT

class AdmissionStateTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='admission-fixture-');self.path=Path(self.tmp.name)/'quota.json';self.now=[1000.];self.guard=Admission(self.path,clock=lambda:self.now[0])
 def tearDown(self):self.tmp.cleanup()
 def seed(self,role='reasoning',headers=None,status=200):
  ticket=self.guard.admit(role,100)
  self.guard.observe(role,ticket,headers or {'x-ratelimit-limit-requests':'1000','x-ratelimit-remaining-requests':'10','x-ratelimit-reset-requests':'1h','x-ratelimit-limit-tokens':'8000','x-ratelimit-remaining-tokens':'4000','x-ratelimit-reset-tokens':'30s'},status)
  self.guard.finish(role,ticket)
 def test_duration_headers_parse_only_bounded_numeric_units(self):
  self.assertAlmostEqual(duration('2m59.56s'),179.56);self.assertEqual(duration('500ms'),.5);self.assertEqual(duration('1h2m3s'),3723)
  for value in [None,'nan','-1','inf','1e3','90000','1sPRIVATE',True]:self.assertIsNone(duration(value))
 def test_estimate_includes_max_output_and_is_not_a_tokenizer_claim(self):
  p={'json':{'messages':[{'role':'user','content':'雨が降る理由'}],'max_completion_tokens':1024}}
  self.assertGreater(estimated_tokens('reasoning',p),1024);self.assertEqual(estimated_tokens('asr',p),0)
  for n in [True,0,-1,'1024',65537]:
   with self.assertRaises(AdmissionDenied):estimated_tokens('reasoning',{'json':{'max_completion_tokens':n}})
  with self.assertRaises(AdmissionDenied):estimated_tokens('reasoning',{'json':{'text':'x'*524289}})
 def test_unknown_balance_does_not_become_an_invented_allowance(self):
  ticket=self.guard.admit('reasoning',1500);state=json.loads(self.path.read_bytes())['roles']['reasoning']
  self.assertIsNone(state['remaining_tokens']);self.assertIsNone(state['remaining_requests']);self.guard.finish('reasoning',ticket)
 def test_shared_observation_rejects_known_token_shortfall_without_mutation(self):
  self.seed();before=self.path.read_bytes();other=Admission(self.path,clock=lambda:self.now[0])
  with self.assertRaises(AdmissionDenied) as caught:other.admit('reasoning',4001)
  self.assertEqual(caught.exception.reason,'known_token_balance');self.assertEqual(self.path.read_bytes(),before)
 def test_expiry_allows_a_new_explicit_attempt_without_inventing_refill(self):
  self.seed();self.now[0]+=31;ticket=self.guard.admit('reasoning',2000)
  state=json.loads(self.path.read_bytes())['roles']['reasoning'];self.assertIsNone(state['remaining_tokens']);self.assertEqual(state['remaining_requests'],9);self.guard.finish('reasoning',ticket)
 def test_RPD_is_not_released_by_the_token_minute_reset(self):
  self.seed(headers={'x-ratelimit-limit-requests':'1000','x-ratelimit-remaining-requests':'0','x-ratelimit-reset-requests':'1h','x-ratelimit-remaining-tokens':'0','x-ratelimit-reset-tokens':'2s'})
  self.now[0]+=61
  with self.assertRaises(AdmissionDenied) as caught:self.guard.admit('reasoning',100)
  self.assertEqual(caught.exception.reason,'known_request_balance')
 def test_failed_or_canceled_attempt_retains_conservative_local_forecast(self):
  self.seed();ticket=self.guard.admit('reasoning',200);self.guard.finish('reasoning',ticket)
  state=json.loads(self.path.read_bytes())['roles']['reasoning'];self.assertEqual(state['remaining_tokens'],3800);self.assertEqual(state['remaining_requests'],9)
 def test_shared_lease_blocks_concurrency_and_old_owner_cannot_clear_new_lease(self):
  first=self.guard.admit('reasoning',100);other=Admission(self.path,clock=lambda:self.now[0])
  with self.assertRaises(AdmissionDenied) as caught:other.admit('reasoning',100)
  self.assertEqual(caught.exception.reason,'model_request_active')
  self.now[0]+=61;second=other.admit('reasoning',100);self.guard.finish('reasoning',first)
  self.assertEqual(json.loads(self.path.read_bytes())['roles']['reasoning']['lease']['id'],second);other.finish('reasoning',second)
 def test_malformed_state_fails_closed_without_replacing_it(self):
  for raw in [b'PRIVATE corrupt',b'[]',b'{"version":true,"updated_at":1000,"roles":{}}']:
   self.path.write_bytes(raw)
   with self.assertRaisesRegex(AdmissionDenied,'provider_admission_denied'):self.guard.admit('reasoning',1)
   self.assertEqual(self.path.read_bytes(),raw)
 def test_clock_reversal_does_not_erase_existing_reservations(self):
  self.seed();before=self.path.read_bytes();self.now[0]-=1
  with self.assertRaises(AdmissionDenied) as caught:self.guard.admit('reasoning',1)
  self.assertEqual(caught.exception.reason,'clock_reversed');self.assertEqual(self.path.read_bytes(),before)
 def test_reduced_valid_limit_cannot_be_ignored_by_an_old_higher_balance(self):
  self.seed();ticket=self.guard.admit('reasoning',1);self.guard.observe('reasoning',ticket,{'x-ratelimit-limit-tokens':'50'},200);self.guard.finish('reasoning',ticket)
  with self.assertRaises(AdmissionDenied) as caught:self.guard.admit('reasoning',51)
  self.assertEqual(caught.exception.reason,'estimated_request_too_large')
 def test_429_missing_or_bad_retry_after_uses_bounded_conservative_cooldown(self):
  for value in [None,'PRIVATE nonsense','-3']:
   guard=Admission(clock=lambda:self.now[0]);ticket=guard.admit('asr',0);guard.observe('asr',ticket,{'retry-after':value},429);guard.finish('asr',ticket)
   with self.assertRaises(AdmissionDenied):guard.admit('asr',0)
   self.now[0]+=61;guard.finish('asr',guard.admit('asr',0))
 def test_ASR_and_reasoning_observations_are_independent_and_ASR_tokens_ignored(self):
  self.seed(role='asr',headers={'x-ratelimit-remaining-tokens':'0','x-ratelimit-reset-tokens':'60s'},status=429)
  ticket=self.guard.admit('reasoning',100);self.guard.finish('reasoning',ticket)
  self.assertIsNone(json.loads(self.path.read_bytes())['roles']['asr']['remaining_tokens'])
 def test_header_and_prompt_secrets_never_enter_persisted_metadata(self):
  self.seed(headers={'Authorization':'PRIVATE_KEY','x-ratelimit-remaining-tokens':'PRIVATE_PROMPT','x-ratelimit-limit-requests':'1000'})
  self.assertNotIn('PRIVATE',self.path.read_text())

class AdmissionIntegrationTests(unittest.IsolatedAsyncioTestCase):
 async def test_owned_transport_denial_precedes_session_creation_and_http(self):
  now=[1000.];guard=Admission(clock=lambda:now[0]);ticket=guard.admit('reasoning',1);guard.observe('reasoning',ticket,{'retry-after':'60'},429);guard.finish('reasoning',ticket)
  t=g.Transport(lambda *a,**k:None,key='PRIVATE',opt_in=True,admission=guard)
  with patch.object(g.aiohttp,'ClientSession') as factory:
   with self.assertRaises(AdmissionDenied):await t.request('reasoning',json={})
   factory.assert_not_called();self.assertIsNone(t.session);self.assertFalse(t.active)
 async def test_both_reasoners_release_lock_on_local_denial_without_repair_or_retry(self):
  guard=Admission();ticket=guard.admit('reasoning',1);guard.observe('reasoning',ticket,{'retry-after':'60'},429);guard.finish('reasoning',ticket)
  for cls in [g.GroqReasoning,JSONGroqReasoning]:
   session=f.Session(f.Reply());t=g.Transport(lambda *a,**k:None,key='PRIVATE',session=session,admission=guard);model=cls(f.Events(),transport=t)
   with self.assertRaises(AdmissionDenied):await model.decide([],[],[])
   self.assertFalse(model.lock.locked());self.assertFalse(t.active);self.assertEqual(session.calls,[])
 async def test_cancel_releases_lease_and_active_flag_without_refunding_forecast(self):
  guard=Admission();reply=f.Reply();reply.headers={'x-ratelimit-remaining-requests':'4','x-ratelimit-reset-requests':'60s'};entered=asyncio.Event()
  async def wait(_):entered.set();await asyncio.Event().wait()
  reply.content.read.side_effect=wait;t=g.Transport(lambda *a,**k:None,key='PRIVATE',session=f.Session(reply),admission=guard)
  task=asyncio.create_task(t.request('reasoning',json={}));await entered.wait();task.cancel()
  with self.assertRaises(asyncio.CancelledError):await task
  self.assertFalse(t.active);self.assertIsNone(guard.memory['roles']['reasoning']['lease']);self.assertEqual(guard.memory['roles']['reasoning']['remaining_requests'],4)
 async def test_cleanup_fault_never_masks_user_cancellation_or_logs_private_details(self):
  class Fault(Admission):
   def finish(self,*args):raise ValueError('PRIVATE_DETAIL')
  reply=f.Reply();entered=asyncio.Event();events=f.Events()
  async def wait(_):entered.set();await asyncio.Event().wait()
  reply.content.read.side_effect=wait;t=g.Transport(events.emit,key='PRIVATE',session=f.Session(reply),admission=Fault())
  task=asyncio.create_task(t.request('asr'));await entered.wait();task.cancel()
  with self.assertRaises(asyncio.CancelledError):await task
  self.assertFalse(t.active);self.assertNotIn('PRIVATE',str(events.rows))
 async def test_project_reservation_is_not_refunded_after_local_admission_denial(self):
  tmp=tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='admission-budget-fixture-')
  try:
   folder=Path(tmp.name);fixture=m.ConfigTests();fixture.folder=folder
   _,_,grant,_=fixture.grant();from me3_assistant.guard import SharedBudget
   limits={'asr':72,'reasoning':64,'tts':102,'commit_item':1};budget=SharedBudget(grant,limits,folder);self.assertEqual(budget.reserve('reasoning'),63)
   guard=Admission();ticket=guard.admit('reasoning',1);guard.observe('reasoning',ticket,{'retry-after':'60'},429);guard.finish('reasoning',ticket)
   session=f.Session(f.Reply());t=g.Transport(lambda *a,**k:None,key='PRIVATE',session=session,admission=guard)
   with self.assertRaises(AdmissionDenied):await t.request('reasoning',json={})
   self.assertEqual(json.loads((folder/'budget.json').read_bytes())['reasoning'],63);self.assertEqual(session.calls,[])
  finally:tmp.cleanup()

if __name__=='__main__':unittest.main()
