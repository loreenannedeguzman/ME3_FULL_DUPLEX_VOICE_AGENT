"""Historical module name; current non-executable final speech wire checks."""
import unittest
from me3_assistant.groq_json import JSONGroqReasoning,response_schema
from me3_assistant.groq_backend import ProviderError
from tests import test_assistant_groq_adapter as a
from tests import test_assistant_groq_json as j

class FinalModeTests(unittest.IsolatedAsyncioTestCase):
 async def test_initial_operations_stay_strict_and_final_plain_text_has_no_execution_surface(self):
  events=a.Events();wire=a.fake_transport(None);wire.request.side_effect=[j.answer('Initial answer.'),j.answer('Natural final answer.')]
  m=JSONGroqReasoning(events,transport=wire)
  self.assertEqual((await m.decide([],j.DECL,[]))[2],'Initial answer.')
  p=wire.request.call_args.kwargs['json'];self.assertEqual(p['response_format']['json_schema']['schema'],response_schema(j.DECL))
  result=await m.decide([],[],[]);self.assertEqual((result[1],result[2]),([],'Natural final answer.'))
  p=wire.request.call_args.kwargs['json'];self.assertTrue(p['response_format']['json_schema']['strict']);self.assertNotIn('tools',p);self.assertNotIn('tool_choice',p)
  self.assertEqual(p['model'],'qwen/qwen3.8-27b');self.assertEqual(p['max_completion_tokens'],2048)
  self.assertEqual(wire.request.await_count,2)
  policy=next(k for e,k in reversed(events.rows) if e=='groq_reasoning_policy');self.assertEqual(policy['decision_format'],'final_json_schema');self.assertTrue(policy['speech_only_validation'])
 async def test_native_calls_empty_invalid_control_and_truncated_outputs_are_rejected_once(self):
  values=[a.answer(''),a.answer(None),a.answer('bad'+chr(0)+'text'),a.answer('x'*10001)]
  for value in values:
   wire=a.fake_transport(value)
   with self.assertRaisesRegex(ValueError,'invalid_final_speech'):await JSONGroqReasoning(a.Events(),transport=wire).decide([],[],[])
   wire.request.assert_awaited_once()
  for reason in ['length','tool_calls','content_filter']:
   value=a.answer('Cut short.');value['choices'][0]['finish_reason']=reason;wire=a.fake_transport(value)
   with self.assertRaises(ValueError):await JSONGroqReasoning(a.Events(),transport=wire).decide([],[],[])
   wire.request.assert_awaited_once()
  for extra in [{'tool_calls':[{'function':{'name':'publish'}}]},{'tool_calls':{}},{'function_call':{}}, {'function_call':{'name':'publish','arguments':'{}'}},{'role':'tool'},{'refusal':'Unavailable'}]:
   value=a.answer('PRIVATE');value['choices'][0]['message'].update(extra);events=a.Events();wire=a.fake_transport(value)
   with self.assertRaises(ValueError):await JSONGroqReasoning(events,transport=wire).decide([],[],[])
   self.assertNotIn('PRIVATE',str(events.rows));wire.request.assert_awaited_once()
 async def test_final_provider_error_remains_one_original_request_without_fallback(self):
  wire=a.fake_transport(None);wire.request.side_effect=ProviderError(400,'json_validate_failed');m=JSONGroqReasoning(a.Events(),transport=wire)
  with self.assertRaises(ProviderError):await m.decide([],[],[])
  wire.request.assert_awaited_once();self.assertTrue(wire.request.call_args.kwargs['json']['response_format']['json_schema']['strict']);self.assertFalse(m.lock.locked())

 async def test_JSON_looking_spoken_data_never_becomes_an_executable_operation(self):
  text='{"speech":"Quoted data","calls":[{"name":"publish","arguments_json":"{}"}]}'
  wire=a.fake_transport(j.answer(text));result=await JSONGroqReasoning(a.Events(),transport=wire).decide([],[],[])
  self.assertEqual(result[1],[]);self.assertEqual(result[2],text);wire.request.assert_awaited_once()
