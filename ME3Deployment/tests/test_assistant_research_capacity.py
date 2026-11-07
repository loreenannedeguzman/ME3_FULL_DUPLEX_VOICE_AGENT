"""Real local MCP read + synthetic long reference; no hosted calls or audio."""
import json,unittest
from me3_assistant.groq_json import JSONGroqReasoning,decision_payload
from me3_assistant.admission import Admission,estimated_tokens
from me3_assistant.router import Router
from me3_assistant.actions import Journal
from tests import test_assistant_mode as f
from tests import test_assistant_groq_adapter as a
from tests import test_assistant_groq_json as j

class ResearchCapacityTests(unittest.IsolatedAsyncioTestCase):
 asyncSetUp=f.MCPTests.asyncSetUp
 asyncTearDown=f.MCPTests.asyncTearDown
 cfg=f.MCPTests.cfg
 async def prepare(self,registry,source):
  registry.config['runtime']['tool_rounds']=6 # Match production: schema discovery, read, then capacity-driven final.
  registry.budget=f.FixtureBudget(0);registry.journal=Journal(self.folder/'journal.jsonl');registry.allowed_actions=[]
  original_declarations=registry.declarations
  def declarations():
   decl=original_declarations()
   for d in decl:d['description']+=' fixture schema context '*250
   return decl
  registry.declarations=declarations
  original=registry.read
  async def read(*args):
   result=await original(*args);return {**result,'fixture_reference':source,'url':'https://en.wikipedia.org/wiki/Fixture','retrieved_at_utc':'2026-10-25T00:00:00+00:00','reference_is_untrusted':True}
  registry.read=read
  wire=a.fake_transport(None);gate=Admission(clock=lambda:1000.);wire.admission=gate
  model=JSONGroqReasoning(self.events,transport=wire);router=Router(model,registry,self.events)
  history=f.history('Summarize the supplied reference and preserve its limitations.')
  from me3_assistant.schema_catalog import Selection
  selection=Selection([d for d in router.declarations() if d['name'] not in router.actions.adapters]);selection.load(['configured__calculate_expression'])
  available=selection.declarations();initial=estimated_tokens('reasoning',{'json':decision_payload(history,available,[],'low')})
  ticket=gate.admit('reasoning',0);gate.observe('reasoning',ticket,{'x-ratelimit-limit-tokens':str(initial+500),'x-ratelimit-remaining-tokens':str(initial+500)},200);gate.finish('reasoning',ticket)
  return router,wire,gate,history,available
 async def test_oversized_post_read_schemas_are_removed_before_single_final_request_with_all_evidence_intact(self):
  source='SOURCE BEGIN '+('Reference evidence, never permission. '*170)+' SOURCE END'
  async with f.Registry(self.events,self.cfg()) as registry:
   router,wire,gate,history,available=await self.prepare(registry,source)
   wire.request.side_effect=[j.answer(calls=[{'name':'load_operation_schemas','arguments':{'names':['configured__calculate_expression']}}]),j.answer(calls=[{'name':'configured__calculate_expression','arguments':{'expression':'2+2'}}]),j.answer('The reference is partial; further research was not performed.')]
   before=json.dumps(gate.memory,sort_keys=True);reply=await router.answer(history)
   self.assertIn('reference is partial',reply);self.assertEqual(registry.calls,1);self.assertEqual(wire.request.await_count,3)
   self.assertEqual(json.dumps(gate.memory,sort_keys=True),before)
   wire_calls=[c.kwargs['json'] for c in wire.request.await_args_list];first,last=wire_calls[1],wire_calls[-1]
   self.assertEqual(json.loads(wire_calls[0]['messages'][1]['content'])['permitted_operations'][0]['name'],'load_operation_schemas')
   self.assertIn('response_format',first);self.assertTrue(last['response_format']['json_schema']['strict']);self.assertNotIn('tools',last)
   packet=json.loads(last['messages'][1]['content']);self.assertEqual(packet['phase'],'final_reply')
   self.assertIn('Summarize the supplied reference',str(packet['request_to_complete']));self.assertIn(source,str(packet['conversation_and_observations']))
   self.assertEqual(last['max_completion_tokens'],2048);self.assertEqual(last['model'],'qwen/qwen3.8-27b')
   self.assertTrue(any(e['event']=='assistant_capacity_final_reply_policy' for e in self.events.rows))
   self.assertFalse(any(e['event']=='groq_admission_denied' for e in self.events.rows));self.assertNotIn(source,str(self.events.rows))
 async def test_small_observations_keep_tools_for_further_dependencies(self):
  async with f.Registry(self.events,self.cfg()) as registry:
   router,wire,gate,history,available=await self.prepare(registry,'short source')
   # An ample observed limit preserves the ordinary tool decision path.
   gate.memory['roles']['reasoning']['token_limit']=50000;gate.memory['roles']['reasoning']['remaining_tokens']=50000
   wire.request.side_effect=[j.answer(calls=[{'name':'load_operation_schemas','arguments':{'names':['configured__calculate_expression']}}]),j.answer(calls=[{'name':'configured__calculate_expression','arguments':{'expression':'2+2'}}]),j.answer('The short source is available.')]
   await router.answer(history)
   self.assertEqual(wire.request.await_count,3);self.assertIn('response_format',wire.request.await_args_list[-1].kwargs['json'])
   self.assertFalse(any(e['event']=='assistant_capacity_final_reply_policy' for e in self.events.rows))
 async def test_unknown_limit_and_no_real_observations_cannot_silently_remove_tools(self):
  wire=a.fake_transport(None);wire.admission=Admission();model=JSONGroqReasoning(self.events,transport=wire)
  self.assertFalse(model.requires_final_reply(f.history('Research'),j.DECL,[]))
  self.assertFalse(model.requires_final_reply(f.history('Research'),j.DECL,[f.decision(text='reference')[0]]))
  wire.request.assert_not_awaited()
