"""Greedy tool planning and fail-closed malformed JSON; no hosted calls."""
import json,unittest
from me3_assistant.groq_backend import GroqReasoning
from me3_assistant.router import Router
from me3_assistant.actions import Journal
from tests import test_assistant_groq_adapter as a
from tests import test_assistant_mode as f

class DeterministicPayloadTests(unittest.IsolatedAsyncioTestCase):
 async def test_explicit_greedy_payload_keeps_model_limits_and_single_request(self):
  transport=a.fake_transport(a.answer());model=GroqReasoning(a.Events(),transport=transport)
  await model.decide([],[],[]);payload=transport.request.call_args.kwargs['json']
  self.assertEqual(payload['temperature'],0);self.assertEqual(payload['reasoning_effort'],'low')
  self.assertEqual(payload['max_completion_tokens'],1024);self.assertEqual(payload['model'],'openai/gpt-oss-20b')
  self.assertEqual(transport.request.await_count,1)

class InvalidToolJSONTests(unittest.IsolatedAsyncioTestCase):
 asyncSetUp=f.MCPTests.asyncSetUp
 asyncTearDown=f.MCPTests.asyncTearDown
 cfg=f.MCPTests.cfg
 async def test_trailing_quote_is_never_repaired_or_dispatched(self):
  async with f.Registry(self.events,self.cfg('extension')) as r:
   r.budget=f.FixtureBudget(0);r.journal=Journal(self.folder/'journal.jsonl');r.allowed_actions=[]
   args=json.dumps({'expression':'2+2'})+'"'
   response=a.answer(calls=[a.call('extension__calculate',args)])
   transport=a.fake_transport(response);router=Router(GroqReasoning(self.events,transport=transport),r,self.events)
   with self.assertRaises(ValueError):await router.answer(f.history('A public arithmetic request'))
   self.assertEqual(transport.request.await_count,1);self.assertEqual(r.calls,0)
   self.assertEqual(r.journal.records,{});self.assertIsNone(router.pending)

if __name__=='__main__':unittest.main()
