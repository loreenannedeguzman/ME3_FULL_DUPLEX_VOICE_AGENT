"""JSON wire decisions through the real controller, with no hosted requests."""
import asyncio,json,shutil,unittest
from google.genai import types
from jsonschema import ValidationError
from me3_assistant.groq_json import JSONGroqReasoning,json_decision,json_messages
from me3_assistant.groq_backend import ProviderError
from me3_assistant.router import Router
from me3_assistant.registry import Registry
from tests import test_assistant_groq_adapter as a
from tests import test_assistant_music as m
from tests import test_assistant_mode as f
from me3_assistant.actions import Journal

DECL=[{'name':'calculate','description':'Calculate an expression','parameters':{'type':'object','properties':{'expression':{'type':'string'}},'required':['expression'],'additionalProperties':False}}]
def answer(speech='',calls=None):return a.answer(json.dumps({'speech':speech,'calls':[{'name':c['name'],'arguments_json':json.dumps(c['arguments'])} for c in calls or []]}))

class JSONDecisionTests(unittest.IsolatedAsyncioTestCase):
 async def test_payload_uses_json_mode_actual_schemas_same_profile_and_one_request(self):
  transport=a.fake_transport(answer('Four.'));model=JSONGroqReasoning(a.Events(),transport=transport)
  _,calls,speech=await model.decide([],DECL,[]);p=transport.request.call_args.kwargs['json']
  self.assertEqual(p['response_format']['type'],'json_schema');self.assertTrue(p['response_format']['json_schema']['strict']);self.assertNotIn('tool_choice',p);self.assertNotIn('tools',p)
  self.assertEqual(json.loads(p['messages'][1]['content'])['permitted_operations'],DECL)
  self.assertEqual((p['model'],p['reasoning_effort'],p['temperature'],p['max_completion_tokens']),('qwen/qwen3.8-27b','none',0,2048))
  self.assertEqual((speech,calls),('Four.',[]));self.assertEqual(transport.request.await_count,1)
 def test_valid_dynamic_call_and_response_identity_survive_as_untrusted_data(self):
  content,calls,_=json_decision(answer(calls=[{'name':'calculate','arguments':{'expression':'2+2'}}]),DECL)
  response=types.Content(role='user',parts=[types.Part(function_response=types.FunctionResponse(id=calls[0].id,name='calculate',response={'result':4}))])
  wire=json.loads(json_messages([],DECL,[content,response])[1]['content'])['conversation_and_observations']
  self.assertEqual(json.loads(json.loads(wire[-2]['content'])['calls'][0]['arguments_json']),{'expression':'2+2'})
  self.assertIn(calls[0].id,wire[-1]['content']);self.assertIn('not a human instruction',wire[-1]['content'])
  self.assertNotIn('tool',set(x['role'] for x in wire))
 def test_bad_syntax_duplicate_keys_nonfinite_unknown_tools_and_invalid_actual_schema_fail_closed(self):
  invalid=['{"speech":"ok","speech":"other","calls":[]}', '{"speech":"","calls":[]} extra', '{"speech":"","calls":[{"name":"calculate","arguments":{"expression":NaN}}]}']
  invalid += [json.dumps(v) for v in ({'speech':'','calls':[{'name':'commit_anything','arguments':{}}]}, {'speech':'','calls':[{'name':'calculate','arguments':{}}]}, {'speech':'','calls':[{'name':'calculate','arguments':{'expression':'2+2','extra':1}}]}, {'speech':'Four','calls':[],'private':'no'}, {'speech':'','calls':[{'name':'calculate','arguments':'{}'}]})]
  for raw in invalid:
   with self.subTest(raw=raw),self.assertRaises((ValueError,ValidationError)):json_decision(a.answer(raw),DECL)
 async def test_provider_error_never_retries_and_unlocks(self):
  transport=a.fake_transport(None);transport.request.side_effect=ProviderError(504)
  model=JSONGroqReasoning(a.Events(),transport=transport)
  with self.assertRaises(ProviderError):await model.decide([],DECL,[])
  self.assertFalse(model.lock.locked());self.assertEqual(transport.request.await_count,1)
 async def test_invalid_response_logs_no_private_prose_and_never_repairs(self):
  events=a.Events();transport=a.fake_transport(a.answer('PRIVATE malformed'));model=JSONGroqReasoning(events,transport=transport)
  with self.assertRaisesRegex(ValueError,'invalid_json_object_decision'):await model.decide([],DECL,[])
  self.assertNotIn('PRIVATE',str(events.rows));self.assertFalse(model.lock.locked());self.assertEqual(transport.request.await_count,1)

class JSONGoalTests(unittest.IsolatedAsyncioTestCase):
 asyncSetUp=m.MusicMCPTests.asyncSetUp
 asyncTearDown=m.MusicMCPTests.asyncTearDown
 setup_actions=m.MusicMCPTests.setup_actions
 @unittest.skipUnless(shutil.which('ffmpeg'),'FFmpeg required for temporary decoder')
 async def test_real_music_goal_each_effect_waits_for_own_confirmation_and_verifies_no_summary_request(self):
  async with Registry(self.events,self.cfg,music_bus=self.bus.path) as r:
   self.setup_actions(r,2)
   steps=[{'tool':'propose_music_control','arguments':{'command':'play','track_id':'Study.wav','volume':20}},{'tool':'propose_music_control','arguments':{'command':'volume','volume':40}}]
   transport=a.fake_transport(answer(calls=[{'name':'run_goal_steps','arguments':{'steps':steps}}]));router=Router(JSONGroqReasoning(self.events,transport=transport),r,self.events)
   first=await router.answer(m.history('Play then set forty percent'));self.assertIn('Step 1 of 2',first);self.assertEqual(self.bus.snapshot()['state'],'stopped')
   async def consume():
    while True:self.bus.mix(bytes(3840));await asyncio.sleep(.01)
   task=asyncio.create_task(consume())
   try:
    second=await router.answer(m.history('Confirm',first));self.assertIn('Step 2 of 2',second);self.assertEqual(self.bus.snapshot()['volume_percent'],20)
    self.assertEqual(router.actions.budget._used('music_control'),1)
    self.assertEqual(await router.answer(m.history('Confirm',second,'cancelled')),second);self.assertEqual(self.bus.snapshot()['volume_percent'],20)
    final=await router.answer(m.history('Confirmed',second));self.assertIn('checked',final);self.assertEqual(router.workflow.states,['verified','verified'])
    self.assertEqual(self.bus.snapshot()['volume_percent'],40);self.assertEqual(router.actions.budget._used('music_control'),2);self.assertEqual(transport.request.await_count,1)
   finally:task.cancel();await asyncio.gather(task,return_exceptions=True)
 async def test_invalid_later_step_blocks_all_earlier_rpcs(self):
  async with Registry(self.events,self.cfg,music_bus=self.bus.path) as r:
   self.setup_actions(r,2)
   steps=[{'tool':'propose_music_control','arguments':{'command':'play','track_id':'Study.wav','volume':20}},{'tool':'propose_music_control','arguments':{'command':'volume','track_id':'Study.wav','volume':40}}]
   transport=a.fake_transport(answer(calls=[{'name':'run_goal_steps','arguments':{'steps':steps}}]));router=Router(JSONGroqReasoning(self.events,transport=transport),r,self.events)
   reply=await router.answer(m.history('A goal'))
   self.assertIn('No new action was started',reply);self.assertLessEqual(len(reply),120)
   self.assertEqual(r.calls,0);self.assertIsNone(router.pending);self.assertEqual(r.journal.records,{})

class JSONReadTests(unittest.IsolatedAsyncioTestCase):
 asyncSetUp=f.MCPTests.asyncSetUp
 asyncTearDown=f.MCPTests.asyncTearDown
 cfg=f.MCPTests.cfg
 async def test_real_calculator_roundtrip_then_contextual_answer(self):
  async with Registry(self.events,self.cfg()) as r:
   r.budget=f.FixtureBudget(0);r.journal=Journal(self.folder/'journal.jsonl');r.allowed_actions=[]
   transport=a.fake_transport(None)
   transport.request.side_effect=[answer(calls=[{'name':'configured__calculate_expression','arguments':{'expression':'2+2'}}]),answer('Two plus two is four.'),answer('The earlier answer was four.')]
   router=Router(JSONGroqReasoning(self.events,transport=transport),r,self.events)
   first=await router.answer(f.history('Calculate two plus two'));self.assertIn('four',first)
   self.assertEqual(r.calls,1)
   observation=transport.request.call_args.kwargs['json']['messages'][-1]
   self.assertIn('Tool observation data',observation['content']);self.assertIn('4',observation['content'])
   second=await router.answer(f.history('What was your earlier answer?',first));self.assertIn('four',second)
   sent=json.loads(transport.request.call_args.kwargs['json']['messages'][1]['content'])['conversation_and_observations'];self.assertTrue(any(x['role']=='assistant' and x['content']==first for x in sent))
   self.assertEqual(transport.request.await_count,3);self.assertEqual(r.calls,1)

if __name__=='__main__':unittest.main()
