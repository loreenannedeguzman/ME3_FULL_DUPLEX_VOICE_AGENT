"""Real discovery/dispatch boundary and no-wire schema loading; not live acceptance."""
import asyncio,copy,json,shutil,unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from me3_assistant.schema_catalog import Selection,NAME
from me3_assistant.workflow import NAME as GOAL
from me3_assistant.registry import Registry
from me3_assistant.router import Router
from me3_assistant.groq_json import JSONGroqReasoning
from tests import test_assistant_mode as f
from tests import test_assistant_music as music
from tests import test_assistant_standing_music as standing

class SelectionTests(unittest.TestCase):
 def test_load_is_metadata_only_exact_schema_unchanged_and_not_duplicated(self):
  tools=[dict(name='music__list_tracks',description='Actual local catalog. More detail.',parameters={'type':'object','properties':{'title':{'const':'keep'}},'additionalProperties':False})];before=copy.deepcopy(tools);s=Selection(tools)
  self.assertEqual([d['name'] for d in s.declarations()],[NAME]);self.assertNotIn('properties',s.directory()['description']);self.assertIn('music__list_tracks',s.directory()['description'])
  result=s.load(['music__list_tracks']);self.assertEqual(result['executed_operations'],0);self.assertFalse(result['source_content_obtained']);self.assertNotIn('parameters',json.dumps(result));self.assertEqual(s.declarations()[0],tools[0]);self.assertEqual(tools,before)
  s.declarations()[0]['parameters']['properties'].clear();self.assertEqual(s.declarations()[0],tools[0])
 def test_unknown_private_and_malformed_requests_cannot_expand_surface(self):
  s=Selection([dict(name='music__list_tracks',description='Catalog',parameters={})])
  for names in [[],['commit_music_control'],['music__list_tracks']*2,[{}],None]:
   with self.assertRaises(ValueError):s.load(names)
  self.assertEqual(s.loaded,set())

class ActualRouterTests(unittest.IsolatedAsyncioTestCase):
 asyncSetUp=music.MusicMCPTests.asyncSetUp
 asyncTearDown=music.MusicMCPTests.asyncTearDown
 def setup(self,r):return standing.ActualMCPTests.setup(self,r)
 async def test_conversation_needs_one_decision_and_no_loading_or_RPC(self):
  async with Registry(self.events,self.cfg) as r:
   self.setup(r);model=f.Model(f.decision(text='Hello.'));model.supports_schema_loading=True;router=Router(model,r,self.events);reply=await router.answer(f.history('Hello'))
   self.assertEqual(reply,'Hello.');self.assertEqual(len(model.seen),1);self.assertEqual(r.calls,0);self.assertEqual([d['name'] for d in model.seen[0][1]],[NAME])
 async def test_unloaded_guessed_operation_and_private_schema_fail_before_RPC(self):
  for call in [f.decision('music__list_tracks',{}),f.decision(NAME,{'names':['commit_music_control']})]:
   async with Registry(self.events,self.cfg) as r:
    self.setup(r);model=f.Model(call);model.supports_schema_loading=True;router=Router(model,r,self.events);reply=await router.answer(f.history('List my music'))
    self.assertIn('safely interpret',reply);self.assertEqual(r.calls,0);self.assertEqual(r.journal.records,{})
 async def test_real_read_after_loading_and_replacement_starts_new_selection(self):
  async with Registry(self.events,self.cfg) as r:
   self.setup(r);model=f.Model(f.decision(NAME,{'names':['music__list_tracks']}),f.decision('music__list_tracks',{}),f.decision(text='One track.'),f.decision(text='Hello.'));model.supports_schema_loading=True;router=Router(model,r,self.events)
   self.assertEqual(await router.answer(f.history('List my music')),'One track.');self.assertEqual(r.calls,1);self.assertEqual(len(model.seen),3);self.assertEqual([d['name'] for d in model.seen[0][1]],[NAME]);self.assertIn('music__list_tracks',[d['name'] for d in model.seen[1][1]])
   await router.answer(f.history('Change topic: hello'));self.assertEqual([d['name'] for d in model.seen[3][1]],[NAME])
 async def test_interrupted_schema_request_does_not_load_dispatch_or_replay(self):
  async with Registry(self.events,self.cfg) as r:
   self.setup(r);entered=asyncio.Event();release=asyncio.Event();current=True
   class Model:
    supports_schema_loading=True
    async def decide(self,*args):entered.set();await release.wait();return f.decision(NAME,{'names':[GOAL]})
   router=Router(Model(),r,self.events);router.controller=SimpleNamespace(clock=SimpleNamespace(current=1),current=lambda g:current)
   task=asyncio.create_task(router.answer(f.history('Play music')));await entered.wait();current=False;release.set();self.assertEqual(await task,'');self.assertEqual(r.calls,0);self.assertEqual(r.journal.records,{})
 @unittest.skipUnless(shutil.which('ffmpeg'),'FFmpeg required for actual temporary decoder')
 async def test_actual_JSON_adapter_load_read_and_standing_play_charge_each_decision_once(self):
  async with Registry(self.events,self.cfg,music_bus=self.bus.path) as r:
   self.setup(r);r.budget=f.FixtureBudget(10);r.config['runtime']['tool_rounds']=6
   def answer(name,args):return {'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':json.dumps({'speech':'','calls':[{'name':name,'arguments_json':json.dumps(args)}]})}}]}
   transport=SimpleNamespace(request=AsyncMock(side_effect=[answer(NAME,{'names':['music__list_tracks',GOAL]}),answer('music__list_tracks',{}),answer(GOAL,{'steps':[{'tool':'propose_music_control','arguments':{'command':'play','track_id':'Study.wav'}}]})]),close=AsyncMock())
   budget=r.budget
   class Model(JSONGroqReasoning):
    async def decide(inner,*args):budget.reserve('reasoning');return await super().decide(*args)
   model=Model(self.events,transport=transport);router=Router(model,r,self.events)
   try:
    reply=await router.answer(f.history('Find and play my local study track.'));self.assertIn('Playback started',reply);self.assertEqual(router.workflow.states,['verified']);self.assertEqual(transport.request.await_count,3);self.assertEqual(budget.used,3);self.assertEqual(router.actions.budget._used(),1);self.assertEqual(r.calls,4)
    first=json.loads(transport.request.call_args_list[0].kwargs['json']['messages'][1]['content']);second=json.loads(transport.request.call_args_list[1].kwargs['json']['messages'][1]['content']);self.assertEqual([d['name'] for d in first['permitted_operations']],[NAME]);self.assertIn(GOAL,[d['name'] for d in second['permitted_operations']]);self.assertNotIn('propose_music_control',[d['name'] for d in second['permitted_operations']])
   finally:await model.close()
