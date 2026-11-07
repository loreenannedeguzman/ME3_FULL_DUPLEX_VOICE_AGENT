"""Profile migration semantics, not a hosted model-quality qualification."""
import asyncio,copy,json,unittest
from me3_assistant.groq_json import JSONGroqReasoning,decision_payload
from me3_assistant.groq_backend import GroqReasoning,ProviderError,Transport
from me3_assistant.admission import Admission,AdmissionDenied,estimated_tokens
from me3_assistant.voice_profile import DECISION_MODEL,DECISION_REASONING_EFFORT
from me3_assistant.runner import runtime_profile
from tests import test_assistant_groq_adapter as a
from tests import test_assistant_groq_json as j

class QwenProfileTests(unittest.IsolatedAsyncioTestCase):
 async def test_normal_initial_and_final_use_explicit_instruct_model_without_native_tools_or_fallback(self):
  wire=a.fake_transport(j.answer('The actual source was unavailable.'));events=a.Events();model=JSONGroqReasoning(events,transport=wire)
  for decl in (j.DECL,[]):
   _,calls,text=await model.decide([],decl,[]);p=wire.request.call_args.kwargs['json']
   self.assertEqual((p['model'],p['reasoning_effort'],p['max_completion_tokens']),(DECISION_MODEL,'none',2048))
   self.assertEqual(DECISION_MODEL,'qwen/qwen3.8-27b');self.assertFalse(p['include_reasoning'])
   self.assertTrue(p['response_format']['json_schema']['strict']);self.assertNotIn('reasoning_format',p)
   self.assertNotIn('tools',p);self.assertNotIn('tool_choice',p);self.assertEqual(calls,[])
  self.assertEqual(wire.request.await_count,2);self.assertEqual(runtime_profile('groq')['reasoning_model'],DECISION_MODEL)
 def test_legacy_adapter_profile_is_preserved_and_new_profile_rejects_implicit_reasoning_mode_changes(self):
  wire=a.fake_transport(None)
  self.assertEqual(GroqReasoning(a.Events(),transport=wire).reasoning_effort,'low')
  with self.assertRaises(ValueError):GroqReasoning(a.Events(),transport=wire,reasoning_effort='none')
  for effort in ('low','medium','high','default'):
   with self.assertRaises(ValueError):JSONGroqReasoning(a.Events(),transport=wire,reasoning_effort=effort)
  wire.request.assert_not_awaited()
 async def test_provider_rejection_cannot_retry_switch_back_or_repeat_read(self):
  wire=a.fake_transport(None);wire.request.side_effect=ProviderError(400,'json_validate_failed');model=JSONGroqReasoning(a.Events(),transport=wire)
  with self.assertRaises(ProviderError):await model.decide([],[],[])
  wire.request.assert_awaited_once();self.assertEqual(wire.request.call_args.kwargs['json']['model'],DECISION_MODEL);self.assertFalse(model.lock.locked())
 async def test_prior_observed_quota_is_not_cleared_or_increased_by_model_constructor_and_denies_before_wire(self):
  gate=Admission(clock=lambda:1000.);ticket=gate.admit('reasoning',0)
  gate.observe('reasoning',ticket,{'x-ratelimit-limit-tokens':'8000','x-ratelimit-remaining-tokens':'1','x-ratelimit-reset-tokens':'61s'},200);gate.finish('reasoning',ticket)
  before=copy.deepcopy(gate.memory);wire=a.Session(a.Reply());transport=Transport(lambda *a,**k:None,key='fixture',session=wire,admission=gate)
  model=JSONGroqReasoning(a.Events(),transport=transport);self.assertEqual(gate.memory,before)
  with self.assertRaises(AdmissionDenied):await model.decide([],[],[])
  self.assertEqual(wire.calls,[]);self.assertEqual(gate.memory,before)
 async def test_cancellation_of_instruct_final_request_releases_lock_without_another_model_call(self):
  entered=asyncio.Event();wire=a.fake_transport(None)
  async def wait(*a,**k):entered.set();await asyncio.Event().wait()
  wire.request.side_effect=wait;model=JSONGroqReasoning(a.Events(),transport=wire)
  task=asyncio.create_task(model.decide([],[],[]));await entered.wait();task.cancel()
  with self.assertRaises(asyncio.CancelledError):await task
  self.assertFalse(model.lock.locked());wire.request.assert_awaited_once()
