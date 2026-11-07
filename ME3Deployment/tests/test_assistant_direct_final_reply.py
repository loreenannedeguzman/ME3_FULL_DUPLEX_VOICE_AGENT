"""Actual MCP reads with the same bounded model wrapper as normal voice/text."""
import json,unittest
from me3_assistant.actions import Journal
from me3_assistant.router import Router
from me3_assistant.groq_json import JSONGroqReasoning
from tests import test_assistant_mode as f
from tests import test_assistant_groq_adapter as a
from tests.test_assistant_groq_json import answer

class Allowance:
 def __init__(self,n):self.credits=n;self.used=0
 def remaining(self,role):return self.credits if role=='reasoning' else 0
 def permitted(self,role):return self.remaining(role)>0
 def reserve(self,role):
  if not self.permitted(role):raise ValueError('local_authorized_budget_exhausted')
  self.credits-=1;self.used+=1

class DirectFinalTests(unittest.IsolatedAsyncioTestCase):
 asyncSetUp=f.MCPTests.asyncSetUp
 asyncTearDown=f.MCPTests.asyncTearDown
 cfg=f.MCPTests.cfg
 def prepare(self,r,n):
  r.budget=Allowance(n);r.journal=Journal(self.folder/'journal.jsonl');r.allowed_actions=[]
 def bounded(self,r,model):
  class Bounded:
   async def decide(self,*args):
    r.budget.reserve('reasoning');return await model.decide(*args)
  return Bounded()
 async def test_exact_three_request_voice_shape_reserves_last_request_for_full_mixed_reply(self):
  async with f.Registry(self.events,self.cfg()) as r:
   self.prepare(r,3);transport=a.fake_transport(None);packets=[]
   async def generated(role,**kw):
    p=json.loads(kw['json']['messages'][1]['content']);packets.append(p)
    if len(packets)==1:return answer('Rain forms as water condenses into droplets.')
    if p['permitted_operations']:return answer(calls=[{'name':'configured__calculate_expression','arguments':{'expression':'.18*250/3'}}])
    return answer('Gravity pulls heavier droplets down. The result is fifteen.')
   transport.request.side_effect=generated
   router=Router(self.bounded(r,JSONGroqReasoning(self.events,transport=transport)),r,self.events)
   first=await router.answer(f.history('Explain rain in one sentence.'))
   h=f.history('What makes those droplets fall, and calculate eighteen percent of two hundred fifty divided by three?',first)
   reply=await router.answer(h)
   self.assertIn('Gravity',reply);self.assertIn('fifteen',reply);self.assertEqual(r.calls,1)
   self.assertEqual((r.budget.used,transport.request.await_count),(3,3));self.assertEqual(packets[-1]['phase'],'final_reply');self.assertEqual(packets[-1]['permitted_operations'],[])
   self.assertEqual(r.journal.records,{})
 async def test_dependent_reads_keep_tools_until_the_last_budgeted_decision(self):
  async with f.Registry(self.events,self.cfg()) as r:
   self.prepare(r,3);seen=[]
   class Model:
    async def decide(self,h,d,c):
     seen.append(d)
     if len(seen)==1:return f.decision('configured__calculate_expression',{'expression':'2+2'})
     if len(seen)==2:
      self_test.assertTrue(d);self_test.assertEqual(c[-1].parts[0].function_response.response['result']['result'],'4')
      return f.decision('configured__calculate_expression',{'expression':'4*2'})
     self_test.assertEqual(d,[]);return f.decision(text='The first result is 4 and the second result is 8.')
   self_test=self
   reply=await Router(self.bounded(r,Model()),r,self.events).answer(f.history('Double the calculated answer.'))
   self.assertIn('8',reply);self.assertEqual(r.calls,2);self.assertEqual(r.budget.used,3)
 async def test_last_configured_round_is_final_even_with_more_provider_allowance(self):
  async with f.Registry(self.events,self.cfg()) as r:
   self.prepare(r,10);seen=[]
   class Model:
    async def decide(self,h,d,c):
     seen.append(d)
     if not d:return f.decision(text='The result is 4; remaining work was not performed.')
     return f.decision('configured__calculate_expression',{'expression':'2+2'})
   reply=await Router(self.bounded(r,Model()),r,self.events).answer(f.history('Calculate and explain.'))
   self.assertIn('remaining work was not performed',reply);self.assertEqual(seen[-1],[]);self.assertEqual(r.calls,1);self.assertEqual(r.budget.used,3)
 async def test_final_boundary_rejects_repeat_without_another_read_or_reasoning_round(self):
  async with f.Registry(self.events,self.cfg()) as r:
   self.prepare(r,2);call=f.decision('configured__calculate_expression',{'expression':'2+2'});m=f.Model(call,call,call)
   reply=await Router(self.bounded(r,m),r,self.events).answer(f.history('Calculate then explain.'))
   self.assertIn('could not produce',reply);self.assertEqual(r.calls,1);self.assertEqual(r.budget.used,2);self.assertEqual(len(m.seen),2);self.assertEqual(m.seen[-1][1],[])
 async def test_new_explicit_turn_gets_tools_again_after_budget_finalization(self):
  async with f.Registry(self.events,self.cfg()) as r:
   self.prepare(r,2);m=f.Model(f.decision('configured__calculate_expression',{'expression':'2+2'}),f.decision(text='The result is four.'),f.decision(text='New topic.'));router=Router(self.bounded(r,m),r,self.events)
   await router.answer(f.history('Calculate.'));self.assertEqual(m.seen[1][1],[])
   # Explicit fixture top-up represents a separately admitted new user request.
   r.budget.credits=2
   self.assertEqual(await router.answer(f.history('New topic please.')),'New topic.');self.assertTrue(m.seen[2][1]);self.assertEqual(r.calls,1)

if __name__=='__main__':unittest.main()
