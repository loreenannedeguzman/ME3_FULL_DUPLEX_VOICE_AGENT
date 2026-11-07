"""Constrained wire decisions, actual MCP reads and fail-closed arguments; no API."""
import asyncio,json,unittest
from me3_assistant.groq_json import JSONGroqReasoning,json_decision,json_messages
from me3_assistant.groq_backend import ProviderError
from me3_assistant.router import Router
from me3_assistant.registry import Registry
from me3_assistant.actions import Journal
from tests import test_assistant_mode as f
from tests import test_assistant_groq_adapter as a
from tests.test_assistant_groq_json import DECL

def wire(speech='',calls=()):
 return a.answer(json.dumps({'speech':speech,'calls':[{'name':n,'arguments_json':args} for n,args in calls]}))

class StrictWireTests(unittest.IsolatedAsyncioTestCase):
 async def test_one_strict_schema_request_without_native_tools_and_unchanged_profile(self):
  t=a.fake_transport(wire('Four.'));m=JSONGroqReasoning(a.Events(),transport=t)
  await m.decide([],DECL,[]);p=t.request.call_args.kwargs['json'];fmt=p['response_format']
  self.assertEqual(fmt['type'],'json_schema');self.assertTrue(fmt['json_schema']['strict'])
  self.assertEqual(p['tool_choice'],'none');self.assertNotIn('tools',p)
  self.assertEqual((p['model'],p['reasoning_effort'],p['temperature'],p['max_completion_tokens']),('openai/gpt-oss-20b','low',0,1024))
  schema=fmt['json_schema']['schema'];self.assertFalse(schema['additionalProperties'])
  call=schema['properties']['calls']['items'];self.assertFalse(call['additionalProperties'])
  self.assertEqual(call['properties']['name']['enum'],['calculate']);self.assertEqual(call['properties']['arguments_json'],{'type':'string'})
  self.assertEqual(t.request.await_count,1)
 def test_encoded_dynamic_arguments_decode_and_validate_against_actual_schema(self):
  _,calls,_=json_decision(wire(calls=[('calculate','{"expression":"18*250/100/3"}')]),DECL)
  self.assertEqual(dict(calls[0].args),{'expression':'18*250/100/3'})
 def test_legacy_object_arguments_no_longer_bypass_wire_contract(self):
  with self.assertRaises(Exception):json_decision(a.answer('{"speech":"","calls":[{"name":"calculate","arguments":{"expression":"2+2"}}]}'),DECL)
 async def test_bad_argument_json_or_schema_never_retries_and_next_request_recovers(self):
  for bad in ['{"expression":"2+2","expression":"3+3"}','{"expression":NaN}','{}','{"expression":"2+2","extra":1}','[]','{"expression":"2+2"} trailing']:
   t=a.fake_transport(None);t.request.side_effect=[wire(calls=[('calculate',bad)]),wire('A new answer.')]
   m=JSONGroqReasoning(a.Events(),transport=t)
   with self.assertRaisesRegex(ValueError,'invalid_json_object_decision'):await m.decide([],DECL,[])
   self.assertFalse(m.lock.locked());self.assertEqual(t.request.await_count,1)
   self.assertEqual((await m.decide([],DECL,[]))[2],'A new answer.')
 async def test_provider_rejection_and_cancellation_keep_one_attempt_and_release_lock(self):
  t=a.fake_transport(None);t.request.side_effect=ProviderError(400,'tool_use_failed');m=JSONGroqReasoning(a.Events(),transport=t)
  with self.assertRaises(ProviderError):await m.decide([],DECL,[])
  self.assertEqual(t.request.await_count,1);self.assertFalse(m.lock.locked())
  entered=asyncio.Event()
  async def wait(*args,**kw):entered.set();await asyncio.Event().wait()
  t.request.side_effect=wait;task=asyncio.create_task(m.decide([],DECL,[]));await entered.wait();task.cancel()
  with self.assertRaises(asyncio.CancelledError):await task
  self.assertFalse(m.lock.locked());self.assertEqual(t.request.await_count,2)

class StrictReadTests(unittest.IsolatedAsyncioTestCase):
 asyncSetUp=f.MCPTests.asyncSetUp
 asyncTearDown=f.MCPTests.asyncTearDown
 cfg=f.MCPTests.cfg
 async def test_real_mcp_calculation_and_contextual_summary_both_parts_no_effect(self):
  async with Registry(self.events,self.cfg()) as r:
   r.budget=f.FixtureBudget(0);r.journal=Journal(self.folder/'journal.jsonl');r.allowed_actions=[]
   t=a.fake_transport(None);t.request.side_effect=[wire(calls=[('configured__calculate_expression','{"expression":"18*250/100/3"}')]),wire('Gravity pulls heavy droplets down; eighteen percent of 250 divided by three is fifteen.')]
   router=Router(JSONGroqReasoning(self.events,transport=t),r,self.events)
   history=f.history('What makes those droplets fall, and calculate eighteen percent of two hundred and fifty divided by three?','Rain forms as water condenses into droplets.')
   reply=await router.answer(history)
   self.assertIn('Gravity',reply);self.assertIn('fifteen',reply);self.assertEqual(r.calls,1);self.assertEqual(t.request.await_count,2)
   payload=t.request.call_args.kwargs['json'];sent=payload['messages']
   self.assertTrue(any('Tool observation data' in str(x) and '15' in str(x) for x in sent));self.assertTrue(any('Rain forms' in str(x) for x in sent))
   conversation=json.loads(sent[1]['content'])['conversation_and_observations']
   decision=json.loads(next(x['content'] for x in conversation if x['role']=='assistant' and 'arguments_json' in x['content']))
   self.assertEqual(json.loads(decision['calls'][0]['arguments_json']),{'expression':'18*250/100/3'})
   self.assertEqual(r.journal.records,{})

if __name__=='__main__':unittest.main()
