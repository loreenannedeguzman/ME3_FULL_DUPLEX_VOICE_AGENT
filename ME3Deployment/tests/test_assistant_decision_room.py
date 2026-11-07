"""No providers/devices: completion room, full admission, no truncated actions/retry."""
import asyncio,json,unittest
from types import SimpleNamespace as NS
from me3_assistant.groq_json import JSONGroqReasoning,json_decision
from me3_assistant.groq_backend import ProviderError,Transport
from me3_assistant.admission import estimated_tokens,Admission,AdmissionDenied
from tests import test_assistant_groq_adapter as a
from tests import test_assistant_groq_json as j

class CompletionRoomTests(unittest.IsolatedAsyncioTestCase):
 async def payload(self):
  wire=a.fake_transport(j.answer('A complete new answer.'));events=a.Events();model=JSONGroqReasoning(events,transport=wire)
  self.assertEqual((await model.decide([],j.DECL,[]))[2],'A complete new answer.')
  wire.request.assert_awaited_once();return wire.request.call_args.kwargs['json'],events
 async def test_initial_request_has_bounded_room_and_only_final_output_same_model_and_strict_schema(self):
  p,events=await self.payload()
  self.assertEqual(p['max_completion_tokens'],2048);self.assertIs(p['include_reasoning'],False)
  self.assertNotIn('reasoning_format',p);self.assertEqual(p['reasoning_effort'],'none');self.assertEqual(p['model'],'qwen/qwen3.8-27b')
  self.assertTrue(p['response_format']['json_schema']['strict']);self.assertNotIn('tool_choice',p);self.assertNotIn('tools',p)
  policy=next(kw for event,kw in events.rows if event=='groq_reasoning_policy');self.assertEqual(policy['max_completion_tokens'],2048)
 async def test_quota_admission_covers_whole_larger_bound_and_denies_shortfall_before_wire(self):
  p,_=await self.payload();forecast=estimated_tokens('reasoning',{'json':p});previous=estimated_tokens('reasoning',{'json':{**p,'max_completion_tokens':1024}})
  self.assertEqual(forecast-previous,1024)
  gate=Admission(clock=lambda:1000.);ticket=gate.admit('reasoning',0);gate.observe('reasoning',ticket,{'x-ratelimit-limit-tokens':'8000','x-ratelimit-remaining-tokens':str(forecast-1),'x-ratelimit-reset-tokens':'60s'},200);gate.finish('reasoning',ticket)
  original=json.dumps(gate.memory,sort_keys=True);s=a.Session(a.Reply());t=Transport(lambda *a,**k:None,key='fixture',session=s,admission=gate)
  with self.assertRaises(AdmissionDenied):await t.request('reasoning',capacity_wait_seconds=20,json=p)
  self.assertEqual(s.calls,[]);self.assertFalse(t.active);self.assertEqual(json.dumps(gate.memory,sort_keys=True),original)
 async def test_truncated_but_parseable_tool_request_is_rejected_without_repair_or_tool_dispatch(self):
  data=j.answer(calls=[{'name':'calculate','arguments':{'expression':'23*7'}}]);data['choices'][0]['finish_reason']='length'
  with self.assertRaisesRegex(ValueError,'incomplete_json_decision'):json_decision(data,j.DECL)
  wire=a.fake_transport(data);events=a.Events();model=JSONGroqReasoning(events,transport=wire)
  with self.assertRaisesRegex(ValueError,'invalid_json_object_decision'):await model.decide([],j.DECL,[])
  wire.request.assert_awaited_once();self.assertFalse(model.lock.locked())
  details=next(k for e,k in events.rows if e=='groq_decision_rejection_detail');self.assertEqual(details['finish_reason'],'length');self.assertEqual(details['reason'],'incomplete_json_decision')
 async def test_provider_400_remains_one_failed_original_request_no_second_attempt_or_model(self):
  wire=a.fake_transport(None);wire.request.side_effect=ProviderError(400,'json_validate_failed');model=JSONGroqReasoning(a.Events(),transport=wire)
  with self.assertRaises(ProviderError):await model.decide([],j.DECL,[])
  wire.request.assert_awaited_once();self.assertEqual(wire.request.call_args.kwargs['json']['model'],'qwen/qwen3.8-27b');self.assertFalse(model.lock.locked())
