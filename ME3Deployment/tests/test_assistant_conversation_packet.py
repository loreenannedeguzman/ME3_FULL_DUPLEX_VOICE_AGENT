"""WB-02 request-size regression; no model, microphone or project counters."""
import json,unittest
from google.genai import types
from me3_assistant.groq_json import JSONGroqReasoning,json_messages,json_decision,response_schema,decision_payload,FRAME,OUTPUT,COVERAGE
from me3_assistant.reasoning import PROMPT
from me3_assistant.admission import estimated_tokens,Admission,AdmissionDenied
from tests.test_assistant_groq_json import DECL,answer
from tests import test_assistant_groq_adapter as f

def history():return [
 {'type':'user_input','content':[{'type':'text','text':'Compare battery storage and pumped hydro briefly.'}]},
 {'type':'model_output','content':[{'type':'text','text':'Batteries respond quickly; pumped hydro stores energy using elevated water.'}]},
 {'type':'user_input','content':[{'type':'text','text':'Keep that comparison and calculate 12 percent of 75.'}]}]

class ConversationPacketTests(unittest.TestCase):
 def test_one_policy_reduces_repeated_wire_cost_without_reducing_output_or_schemas(self):
  wire=json_messages(history(),DECL,[])
  old=FRAME+PROMPT+'\n'+OUTPUT+COVERAGE+' When phase is final_reply, the controller has closed tool selection for this turn: give the complete reply using actual observations and context, with calls empty. Never propose or repeat any operation. Do not invent missing observations or claim unperformed steps completed; explicitly report any remaining task that could not be performed.'
  payload=decision_payload(history(),DECL,[],'none');self.assertEqual(payload['max_completion_tokens'],2048)
  previous={**payload,'messages':[{'role':'system','content':old},wire[1]]}
  self.assertLess(estimated_tokens('reasoning',{'json':payload}),estimated_tokens('reasoning',{'json':previous}))
  self.assertEqual(json.loads(wire[1]['content'])['permitted_operations'],DECL)
 def test_context_correction_and_unusual_tool_schema_are_lossless(self):
  decl=DECL+[{'name':'unfamiliar_tool','description':'User-owned arbitrary integration','parameters':{'type':'object','properties':{'note':{'type':'string','enum':['αβ “引用” \\ file']}},'required':['note'],'additionalProperties':False}}]
  h=history();h[-1]['content'][0]['text']='Actually use forty, and explain the earlier tradeoff. 日本語でも。'
  p=json.loads(json_messages(h,decl,[])[1]['content'])
  self.assertEqual(p['request_to_complete'],h[-1]['content']);self.assertEqual(p['permitted_operations'],decl)
  self.assertEqual(p['conversation_and_observations'][0]['content'],h[0]['content'][0]['text'])
  self.assertEqual(p['conversation_and_observations'][1]['content'],h[1]['content'][0]['text'])
  self.assertNotIn('\\u65e5',json_messages(h,decl,[])[1]['content'])
 def test_tool_result_cannot_replace_complete_original_request(self):
  c=types.Content(role='model',parts=[types.Part(function_call=types.FunctionCall(id='calculation-1',name='calculate',args={'expression':'.12*75'}))])
  r=types.Content(role='user',parts=[types.Part(function_response=types.FunctionResponse(id='calculation-1',name='calculate',response={'result':'9'}))])
  wire=json_messages(history(),[],[c,r]);p=json.loads(wire[1]['content'])
  self.assertEqual(p['phase'],'final_reply');self.assertEqual(p['request_to_complete'],history()[-1]['content']);self.assertIn('9',str(p['conversation_and_observations']))
  self.assertIn('not the last observation',wire[0]['content'])
  self.assertEqual(set(response_schema([])['properties']),{'speech'})
  complete='Batteries respond quickly; pumped hydro uses elevated water. Twelve percent of75is9.'
  _,calls,speech=json_decision(f.answer(json.dumps({'speech':complete})),[])
  self.assertEqual((calls,speech),([],complete))
 def test_smaller_actual_request_fits_existing_observed_balance_guard_still_denies_shortfall(self):
  payload={'json':{'messages':json_messages(history(),DECL,[]),'response_format':{'type':'json_schema'},'max_completion_tokens':1024}}
  amount=estimated_tokens('reasoning',payload);g=Admission(clock=lambda:1000.)
  t=g.admit('reasoning',0);g.observe('reasoning',t,{'x-ratelimit-remaining-tokens':str(amount),'x-ratelimit-reset-tokens':'60s'},200);g.finish('reasoning',t)
  t=g.admit('reasoning',amount);g.finish('reasoning',t)
  with self.assertRaises(AdmissionDenied):g.admit('reasoning',1)
  self.assertEqual(g.memory['roles']['reasoning']['remaining_tokens'],0)

class AdapterPacketTests(unittest.IsolatedAsyncioTestCase):
 async def test_same_model_strict_schema_output_budget_and_single_wire_attempt(self):
  transport=f.fake_transport(answer('Batteries react faster; the calculation is nine.'))
  m=JSONGroqReasoning(f.Events(),transport=transport)
  _,calls,speech=await m.decide(history(),DECL,[]);p=transport.request.call_args.kwargs['json']
  self.assertEqual((p['model'],p['max_completion_tokens'],p['reasoning_effort']),('qwen/qwen3.8-27b',2048,'none'))
  self.assertTrue(p['response_format']['json_schema']['strict']);self.assertNotIn('tool_choice',p)
  self.assertEqual(transport.request.await_count,1);self.assertFalse(calls);self.assertIn('nine',speech)

if __name__=='__main__':unittest.main()
