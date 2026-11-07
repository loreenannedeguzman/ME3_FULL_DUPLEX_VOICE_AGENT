"""Final speech contract through real router/controller; no live providers/audio."""
import asyncio,base64,json,unittest
from jsonschema import Draft202012Validator,ValidationError
from me3_assistant.groq_json import response_schema,json_decision,json_messages,JSONGroqReasoning
from me3_assistant.schema_catalog import NAME as SCHEMA_TOOL
from me3_assistant.router import Router
from me3_assistant.registry import Registry
from me3_assistant.actions import Journal
from me3_assistant.speech import AssistantController
from me3_assistant.gui import Console,row
from tests import test_assistant_groq_adapter as a
from tests import test_assistant_groq_json as j
from tests import test_assistant_mode as f

class FinalSchemaTests(unittest.TestCase):
 def test_final_schema_has_only_speech_and_every_allowed_fixture_parses_locally(self):
  schema=response_schema([]);self.assertEqual(set(schema['properties']),{'speech'});self.assertNotIn('__no_tools_available__',str(schema))
  for reply in ('Which song do you mean?','Your last request replaced the manuscript discussion.','No publishing action was taken.'):
   value={'speech':reply};Draft202012Validator(schema).validate(value)
   _,calls,speech=json_decision(a.answer(json.dumps(value)),[]);self.assertEqual((calls,speech),([],reply))
 def test_old_placeholder_was_remote_valid_but_local_invalid_not_a_live_replay(self):
  old={'type':'object','properties':{'speech':{'type':'string'},'calls':{'type':'array','items':{'type':'object','properties':{'name':{'type':'string','enum':['__no_tools_available__']},'arguments_json':{'type':'string'}},'required':['name','arguments_json'],'additionalProperties':False}}},'required':['speech','calls'],'additionalProperties':False}
  value={'speech':'An explanation','calls':[{'name':'__no_tools_available__','arguments_json':'{}'}]}
  Draft202012Validator(old).validate(value)
  with self.assertRaises(ValidationError):Draft202012Validator(response_schema([])).validate(value)
  with self.assertRaisesRegex(ValueError,'no_tools_permitted'):json_decision(a.answer(json.dumps(value)),[])
 def test_legacy_empty_envelope_only_not_malformed_or_unknown_fields(self):
  self.assertEqual(json_decision(j.answer('Compatible.'),[])[2],'Compatible.')
  for calls in (None,{},'',False,[{'name':'unknown','arguments_json':'{}'}]):
   with self.subTest(calls=calls),self.assertRaises(ValueError):json_decision(a.answer(json.dumps({'speech':'no','calls':calls})),[])
  for value in ({'speech':'reply','calls':[],'hidden':True},{'speech':7},{'speech':'','calls':[]}):
   with self.subTest(value=value),self.assertRaises((ValueError,ValidationError)):json_decision(a.answer(json.dumps(value)),[])
 def test_phase_and_latest_correction_anchor_match_no_tool_contract(self):
  h=[row('user','Previous request'),row('assistant','Previous reply'),row('user','Actually, a different question')]
  wire=json.loads(json_messages(h,[],[])[1]['content']);self.assertEqual(wire['phase'],'final_reply');self.assertEqual(wire['request_to_complete'],h[-1]['content'])
  self.assertEqual(wire['permitted_operations'],[]);self.assertNotIn(h[-1]['content'][0]['text'],str(wire['conversation_and_observations']))

class FinalRouterTests(unittest.IsolatedAsyncioTestCase):
 asyncSetUp=f.MCPTests.asyncSetUp
 asyncTearDown=f.MCPTests.asyncTearDown
 cfg=f.MCPTests.cfg
 async def test_invalid_final_speech_preserves_completed_read_without_repeating_or_leaking(self):
  async with Registry(self.events,self.cfg('extension')) as r:
   r.budget=f.FixtureBudget(0);r.journal=Journal(self.folder/'journal.jsonl');r.allowed_actions=[]
   r.config['runtime']['tool_rounds']=4
   call={'name':'configured__count_words','arguments':{'text':'alpha beta'}};wire=a.fake_transport(None)
   bad=a.answer('PRIVATE');bad['choices'][0]['message']['tool_calls']=[{'function':{'name':'publish'}}]
   wire.request.side_effect=[j.answer(calls=[{'name':SCHEMA_TOOL,'arguments':{'names':['configured__count_words']}}]),j.answer(calls=[call]),j.answer(calls=[call]),bad,j.answer('A fresh answer.')]
   router=Router(JSONGroqReasoning(self.events,transport=wire),r,self.events)
   reply=await router.answer(f.history('Count alpha beta'))
   self.assertIn('could not produce the final explanation',reply);self.assertNotIn('PRIVATE',reply+str(self.events.rows))
   self.assertEqual(r.calls,1);self.assertEqual(wire.request.await_count,4);self.assertEqual(r.journal.records,{})
   self.assertEqual(await router.answer(f.history('A different question',reply)),'A fresh answer.')
   self.assertEqual(r.calls,1);self.assertEqual(wire.request.await_count,5)
 async def test_repeated_read_ends_with_speech_only_contract_without_extra_rpc_or_effect(self):
  async with Registry(self.events,self.cfg('extension')) as r:
   r.budget=f.FixtureBudget(0);r.journal=Journal(self.folder/'journal.jsonl');r.allowed_actions=[]
   r.config['runtime']['tool_rounds']=4
   call={'name':'configured__count_words','arguments':{'text':'alpha beta'}};wire=a.fake_transport(None)
   wire.request.side_effect=[j.answer(calls=[{'name':SCHEMA_TOOL,'arguments':{'names':['configured__count_words']}}]),j.answer(calls=[call]),j.answer(calls=[call]),j.answer('There are two words.')]
   model=JSONGroqReasoning(self.events,transport=wire);router=Router(model,r,self.events)
   self.assertEqual(await router.answer(f.history('Count the words alpha beta')),'There are two words.')
   self.assertEqual(r.calls,1);self.assertEqual(wire.request.await_count,4);self.assertEqual(r.journal.records,{})
   payload=wire.request.call_args.kwargs['json'];self.assertTrue(payload['response_format']['json_schema']['strict']);self.assertEqual(set(response_schema([])['properties']),{'speech'})
   self.assertEqual(json.loads(payload['messages'][1]['content'])['phase'],'final_reply')
 async def test_interruption_discards_stale_final_answer_and_latest_turn_keeps_context(self):
  async with Registry(self.events,self.cfg('extension')) as r:
   r.budget=f.FixtureBudget(0);r.journal=Journal(self.folder/'journal.jsonl');r.allowed_actions=[]
   r.config['runtime']['tool_rounds']=4
   call={'name':'configured__count_words','arguments':{'text':'alpha beta'}};wire=a.fake_transport(None);waiting=asyncio.Event();release=asyncio.Event();seen=[]
   async def request(role,**kw):
    seen.append(kw['json']);index=len(seen)
    if index==1:return j.answer(calls=[{'name':SCHEMA_TOOL,'arguments':{'names':['configured__count_words']}}])
    if index<=3:return j.answer(calls=[call])
    if index==4:
     waiting.set()
     try:await release.wait()
     except asyncio.CancelledError:pass # Simulate a late original result.
     return j.answer('Obsolete full reply.')
    return j.answer('Your replacement is the current request.')
   wire.request.side_effect=request;router=Router(JSONGroqReasoning(self.events,transport=wire),r,self.events);played=[]
   class Speech:
    async def call(self,role,g,**kw):
     if role=='asr':return {'text':'Count alpha beta' if g==1 else 'Actually, replace that with a different topic.'}
     return {'pcm_b64':base64.b64encode(b'\x01\x00'*480).decode()}
    def require_tts_capacity(self,n):pass
   async def play(pcm,g):played.append(g)
   c=AssistantController(Speech(),router,self.events,play);router.controller=c;gui=Console(self.cfg(),{'revision':'fixture'});gui.bind_controller(c)
   try:
    g=c.interrupt();c.submit(bytes(6400),g);old=c.task;await asyncio.wait_for(waiting.wait(),5)
    g=c.interrupt();await asyncio.gather(old,return_exceptions=True);c.submit(bytes(6400),g);await c.task
    self.assertEqual(played,[2]);self.assertEqual(r.calls,1);self.assertEqual(len(seen),5)
    self.assertNotIn('Obsolete full reply.',str(c.history));self.assertNotIn('Obsolete full reply.',str(gui.display_conversation(c.history)))
    self.assertTrue(any(x.get('control_state')=='cancelled' for x in c.history));self.assertIn('replace that',str(seen[-1]['messages']))
   finally:release.set();await c.close()
