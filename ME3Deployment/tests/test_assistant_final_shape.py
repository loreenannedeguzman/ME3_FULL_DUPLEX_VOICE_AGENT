import unittest
from me3_assistant.groq_json import JSONGroqReasoning,final_speech_decision,final_speech_shape
from tests import test_assistant_groq_adapter as a
from tests import test_assistant_groq_json as j

class FinalShapeTests(unittest.IsolatedAsyncioTestCase):
 async def test_nullable_legacy_function_field_is_absence_not_an_invocation_and_preserves_speech(self):
  for fields in ({},{'function_call':None},{'function_call':None,'tool_calls':None,'refusal':None},{'function_call':None,'tool_calls':[]}):
   data=j.answer('A complete source-grounded final answer.');data['choices'][0]['message'].update(fields)
   wire=a.fake_transport(data);model=JSONGroqReasoning(a.Events(),transport=wire)
   content,calls,text=await model.decide([],[],[])
   self.assertEqual((calls,text),([],'A complete source-grounded final answer.'));wire.request.assert_awaited_once()
 async def test_structural_rejection_metadata_disambiguates_failure_without_any_response_values(self):
  variants=[({'content':None},{'content_kind':'null','content_nonblank':False}),({'content':''},{'content_chars':0}),({'function_call':{'name':'PRIVATE_CALL','arguments':'PRIVATE_ARGUMENTS'}},{'function_call_nonnull':True}),({'refusal':'PRIVATE_REFUSAL'},{'refusal_nonnull':True}),({'tool_calls':[{'private':'PRIVATE_CALL'}]},{'tool_calls_kind':'nonempty_list'}),({'content':'PRIVATE'+chr(0)},{'content_has_invalid_controls':True}),({'content':'PRIVATE'+'x'*10000},{'content_chars':10001})]
  for fields,expected in variants:
   data=a.answer('PRIVATE_PROSE');data['choices'][0]['message'].update(fields);events=a.Events();wire=a.fake_transport(data)
   with self.assertRaisesRegex(ValueError,'invalid_final_speech'):await JSONGroqReasoning(events,transport=wire).decide([],[],[])
   detail=next(k for e,k in events.rows if e=='groq_decision_rejection_detail')
   for key,value in expected.items():self.assertEqual(detail[key],value)
   self.assertNotIn('PRIVATE',str(events.rows));self.assertIs(detail['body_retained'],False);wire.request.assert_awaited_once()
 async def test_null_compatibility_does_not_accept_actual_native_calls_empty_refusal_or_wrong_roles(self):
  for extra in [{'function_call':{}},{'function_call':False},{'function_call':{'name':'publish'}},{'refusal':''},{'refusal':'no'},{'role':'tool'},{'tool_calls':{}},{'tool_calls':[{}]}]:
   data=a.answer('PRIVATE');data['choices'][0]['message'].update(extra)
   with self.assertRaises(ValueError):final_speech_decision(data)
 def test_diagnostics_of_arbitrary_bad_envelopes_contain_only_fixed_structural_fields(self):
  for value in (None,[],{'choices':None},{'choices':[]},{'choices':[{'message':'PRIVATE'}]},{'choices':[{'message':{'content':{'PRIVATE':'PRIVATE'},'role':'PRIVATE'}}]}):
   shape=final_speech_shape(value);self.assertNotIn('PRIVATE',str(shape));self.assertTrue(all(v is None or isinstance(v,(str,int,bool)) for v in shape.values()))
