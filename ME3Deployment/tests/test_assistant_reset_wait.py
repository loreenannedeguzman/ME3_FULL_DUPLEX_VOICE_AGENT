"""Observed TPM reset reproduction with fake clock/wire; no hosted/audio calls."""
import asyncio,copy,json,unittest
from unittest.mock import patch
from me3_assistant.admission import AdmissionDenied
from me3_assistant.groq_backend import Transport,ProviderError
from me3_assistant.groq_json import JSONGroqReasoning
from me3_assistant.speech import AssistantController
from me3_assistant.runner import bind_capacity_wait
from tests import test_assistant_capacity_gui as capacity
from tests import test_assistant_groq_adapter as f
from tests import test_assistant_groq_json as j

class WaitTests(unittest.IsolatedAsyncioTestCase):
 async def test_actual34second_reset_waits_once_before_first_lease_or_wire(self):
  now=[1000.];g=capacity.seeded(now,**{'x-ratelimit-remaining-tokens':'3455','x-ratelimit-reset-tokens':'34.042s'})
  before=copy.deepcopy(g.memory);events=f.Events();session=f.Session(f.Reply());t=Transport(events.emit,key='fixture',session=session,admission=g);credit=[];wait=[]
  t.before_capacity_wait=credit.append
  async def sleep(seconds):
   self.assertEqual(session.calls,[]);self.assertEqual(g.memory,before);wait.append(seconds);now[0]+=seconds
  with patch('me3_assistant.groq_backend.asyncio.sleep',sleep):await t.request('reasoning',capacity_wait_seconds=60,json={'max_completion_tokens':6000})
  self.assertEqual(credit,wait);self.assertEqual(len(wait),1);self.assertAlmostEqual(wait[0],34.042)
  self.assertEqual(len(session.calls),1);self.assertIsNone(g.memory['roles']['reasoning']['lease']);self.assertFalse(t.active)
  self.assertFalse(any(e=='groq_admission_denied' for e,k in events.rows));self.assertFalse(any(k.get('automatic_retry') for e,k in events.rows))
 async def test_34second_wait_can_be_canceled_before_any_lease_wire_or_balance_change(self):
  now=[1000.];g=capacity.seeded(now,**{'x-ratelimit-reset-tokens':'34.042s'});before=copy.deepcopy(g.memory);session=f.Session(f.Reply());t=Transport(lambda *a,**k:None,key='fixture',session=session,admission=g);m=JSONGroqReasoning(f.Events(),transport=t);entered=asyncio.Event()
  async def sleep(seconds):entered.set();await asyncio.Event().wait()
  with patch('me3_assistant.groq_backend.asyncio.sleep',sleep):
   task=asyncio.create_task(m.decide([],[],[]));await asyncio.wait_for(entered.wait(),.5);task.cancel()
   with self.assertRaises(asyncio.CancelledError):await task
  self.assertEqual(g.memory,before);self.assertEqual(session.calls,[]);self.assertFalse(m.lock.locked());self.assertFalse(t.active)
 async def test_stop_during_wait_still_prevents_admission_and_dispatch(self):
  now=[1000.];g=capacity.seeded(now,**{'x-ratelimit-reset-tokens':'34.042s'});before=copy.deepcopy(g.memory);session=f.Session(f.Reply());t=Transport(lambda *a,**k:None,key='fixture',session=session,admission=g);t.stop_requested=asyncio.Event()
  async def sleep(seconds):now[0]+=seconds;t.stop_requested.set()
  with patch('me3_assistant.groq_backend.asyncio.sleep',sleep),self.assertRaises(asyncio.CancelledError):await t.request('reasoning',capacity_wait_seconds=60,json={})
  self.assertEqual(g.memory,before);self.assertEqual(session.calls,[])
 async def test_budget_refusal_prevents_sleep_admission_and_wire(self):
  now=[1000.];g=capacity.seeded(now,**{'x-ratelimit-reset-tokens':'34.042s'});before=copy.deepcopy(g.memory);session=f.Session(f.Reply());events=f.Events();t=Transport(events.emit,key='fixture',session=session,admission=g)
  def refuse(seconds):raise AdmissionDenied('capacity_wait_budget')
  t.before_capacity_wait=refuse
  with patch('me3_assistant.groq_backend.asyncio.sleep') as sleep,self.assertRaises(AdmissionDenied):await t.request('reasoning',capacity_wait_seconds=60,json={})
  sleep.assert_not_called();self.assertEqual(session.calls,[]);self.assertEqual(g.memory,before);self.assertFalse(t.active)
  self.assertTrue(any(e=='groq_admission_denied' and k['reason']=='capacity_wait_budget' and k['wire_attempts']==0 for e,k in events.rows))
 async def test_deny_daily_cooldown_impossible_payload_and_later_than60s_reset_without_wait(self):
  for header,tokens in [({'x-ratelimit-remaining-requests':'0'},4000),({},8001),({'x-ratelimit-reset-tokens':'61s'},4000)]:
   now=[1000.];g=capacity.seeded(now,**header);before=json.dumps(g.memory,sort_keys=True);self.assertEqual(g.capacity_delay('reasoning',tokens,60),0);self.assertEqual(json.dumps(g.memory,sort_keys=True),before)
   with self.assertRaises(AdmissionDenied):g.admit('reasoning',tokens)
  now=[1000.];g=capacity.seeded(now);g.memory['roles']['reasoning']['blocked_until']=1060;self.assertEqual(g.capacity_delay('reasoning',4000,60),0)
  with self.assertRaises(AdmissionDenied):g.admit('reasoning',4000)
 async def test_changed_quota_after_wait_does_not_wait_again_or_dispatch(self):
  now=[1000.];g=capacity.seeded(now,**{'x-ratelimit-reset-tokens':'34.042s'});session=f.Session(f.Reply());t=Transport(lambda *a,**k:None,key='fixture',session=session,admission=g);wait=[]
  async def sleep(seconds):wait.append(seconds);now[0]+=seconds;g.memory['roles']['reasoning']['remaining_requests']=0
  with patch('me3_assistant.groq_backend.asyncio.sleep',sleep),self.assertRaises(AdmissionDenied):await t.request('reasoning',capacity_wait_seconds=60,json={})
  self.assertEqual(len(wait),1);self.assertEqual(session.calls,[])
 async def test_real_provider_failure_is_not_retried_after_the_initial_wait(self):
  now=[1000.];g=capacity.seeded(now,**{'x-ratelimit-reset-tokens':'34.042s'});session=f.Session(f.Reply(status=429));t=Transport(lambda *a,**k:None,key='fixture',session=session,admission=g);wait=[]
  async def sleep(seconds):wait.append(seconds);now[0]+=seconds
  with patch('me3_assistant.groq_backend.asyncio.sleep',sleep),self.assertRaises(ProviderError):await t.request('reasoning',capacity_wait_seconds=60,json={})
  self.assertEqual(len(wait),1);self.assertEqual(len(session.calls),1)

class DeadlineTests(unittest.IsolatedAsyncioTestCase):
 async def test_actual_runtime_binding_credits_wait_before_real_transport_admission(self):
  async def play(*a):pass
  events=f.Events();c=AssistantController(None,None,events,play);g=c.interrupt();events.current_generation=g
  now=[1000.];admission=capacity.seeded(now,**{'x-ratelimit-reset-tokens':'34.042s'})
  wire=f.Session(f.Reply(parts=[json.dumps(j.answer('A final explanation.')).encode(),b'']))
  transport=Transport(events.emit,key='fixture',session=wire,admission=admission);model=JSONGroqReasoning(events,transport=transport);bind_capacity_wait(model,c,events)
  async def sleep(seconds):now[0]+=seconds
  try:
   async with asyncio.timeout(1) as timer:
    c.deadlines[g]={'timer':timer,'audio_credited':False};before=timer.when()
    with patch('me3_assistant.groq_backend.asyncio.sleep',sleep):result=await model.decide([],[],[])
    self.assertEqual(result[2],'A final explanation.');self.assertAlmostEqual(timer.when()-before,34.042)
    self.assertEqual(len(wire.calls),1);self.assertTrue(wire.calls[0][1]['json']['response_format']['json_schema']['strict']);self.assertAlmostEqual(c.deadlines[g]['provider_wait_credited'],34.042)
    c.deadlines.clear()
  finally:await model.close();await c.close()
 async def test_only_current_unexpired_generation_receives_actual_wait_credit_up_to60s_total(self):
  async def play(*a):pass
  c=AssistantController(None,None,f.Events(),play);g=c.interrupt()
  try:
   async with asyncio.timeout(100) as timer:
    c.deadlines[g]={'timer':timer,'audio_credited':False};before=timer.when()
    c.credit_provider_wait(g,34.042);c.credit_provider_wait(g,20)
    self.assertAlmostEqual(timer.when()-before,54.042);self.assertAlmostEqual(c.deadlines[g]['provider_wait_credited'],54.042)
    for gen,seconds in [(g,6),(g-1,1),(g,float('nan')),(g,True),(g,0),(g,61)]:
     with self.assertRaises(AdmissionDenied):c.credit_provider_wait(gen,seconds)
    self.assertAlmostEqual(timer.when()-before,54.042);self.assertIn('240s',c.playback_budget_description)
    c.deadlines.clear()
  finally:await c.close()

