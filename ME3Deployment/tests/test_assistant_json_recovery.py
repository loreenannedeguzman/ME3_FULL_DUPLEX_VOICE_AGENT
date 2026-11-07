"""Known JSON decision failures recover on the next user turn; no APIs/audio."""
import asyncio,base64,json,unittest
from types import SimpleNamespace
from me3_assistant.groq_json import JSONGroqReasoning
from me3_assistant.router import Router
from me3_assistant.registry import Registry
from me3_assistant.actions import Journal
from tests import test_assistant_groq_adapter as a
from tests import test_assistant_groq_json as j
from tests import test_assistant_mode as f
from tests import test_assistant_music as m

class JSONRecoveryTests(unittest.IsolatedAsyncioTestCase):
 asyncSetUp=f.MCPTests.asyncSetUp
 asyncTearDown=f.MCPTests.asyncTearDown
 cfg=f.MCPTests.cfg
 def router(self,r,*responses):
  r.budget=f.FixtureBudget(0);r.journal=Journal(self.folder/'journal.jsonl');r.allowed_actions=[]
  transport=a.fake_transport(None);transport.request.side_effect=list(responses)
  return Router(JSONGroqReasoning(self.events,transport=transport),r,self.events),transport
 async def test_malformed_decision_then_new_request_recovers_without_retry_or_rpc(self):
  async with Registry(self.events,self.cfg()) as r:
   router,t=self.router(r,a.answer('PRIVATE invalid JSON'),j.answer('A coherent new answer.'))
   first=await router.answer(f.history('Explain something.'))
   self.assertIn('No new action',first);self.assertEqual(t.request.await_count,1);self.assertEqual(r.calls,0)
   self.assertEqual(await router.answer(f.history('A new question',first)),'A coherent new answer.')
   self.assertEqual(t.request.await_count,2);self.assertEqual(r.calls,0);self.assertNotIn('PRIVATE',json.dumps(self.events.rows))
 async def test_superseded_invalid_decision_returns_no_obsolete_recovery_speech(self):
  async with Registry(self.events,self.cfg()) as r:
   router,t=self.router(r);active=[True]
   router.controller=SimpleNamespace(clock=SimpleNamespace(current=1),current=lambda g:active[0])
   async def late(*args,**kwargs):active[0]=False;return a.answer('invalid JSON')
   t.request.side_effect=late
   self.assertEqual(await router.answer(f.history('An old request')),'');self.assertEqual(t.request.await_count,1)
   self.assertFalse(any(e['event']=='assistant_reasoning_recoverable_failure' for e in self.events.rows));self.assertEqual(r.calls,0)
 async def test_real_controller_stays_usable_after_json_rejection(self):
  from me3_assistant.speech import AssistantController
  async with Registry(self.events,self.cfg()) as r:
   router,t=self.router(r,a.answer('invalid JSON'),j.answer('Tokyo.'));failures=[];played=[]
   class Speech:
    async def call(self,role,g,**kw):
     if role=='asr':return {'text':'A complete question.','generation':g,'request_id':'fixture-asr'}
     return {'pcm_b64':base64.b64encode(b'\x01\x00'*480).decode()}
    def mark_failure(self,*args):failures.append(args)
   async def play(pcm,g):played.append(g)
   c=AssistantController(Speech(),router,self.events,play);router.controller=c
   try:
    for _ in range(2):
     g=c.interrupt();c.submit(bytes(6400),g);await c.task
    self.assertEqual(failures,[]);self.assertEqual(len(played),2);self.assertEqual(t.request.await_count,2)
    self.assertEqual(c.history[-1]['content'][0]['text'],'Tokyo.');self.assertEqual(r.calls,0)
   finally:await c.close()

class InvalidGoalRecoveryTests(unittest.IsolatedAsyncioTestCase):
 asyncSetUp=m.MusicMCPTests.asyncSetUp
 asyncTearDown=m.MusicMCPTests.asyncTearDown
 setup_actions=m.MusicMCPTests.setup_actions
 async def test_invalid_later_action_blocks_all_then_new_valid_goal_can_prepare(self):
  async with Registry(self.events,self.cfg,music_bus=self.bus.path) as r:
   self.setup_actions(r,2)
   play={'tool':'propose_music_control','arguments':{'command':'play','track_id':'Study.wav','volume':20}}
   invalid={'tool':'propose_music_control','arguments':{'command':'volume','volume':True}}
   transport=a.fake_transport(None);transport.request.side_effect=[j.answer(calls=[{'name':'run_goal_steps','arguments':{'steps':[play,invalid]}}]),j.answer(calls=[{'name':'run_goal_steps','arguments':{'steps':[play]}}])]
   router=Router(JSONGroqReasoning(self.events,transport=transport),r,self.events)
   first=await router.answer(m.history('An invalid goal'))
   self.assertIn('No new action',first);self.assertEqual(r.calls,0);self.assertIsNone(router.pending)
   self.assertEqual(r.journal.records,{});self.assertEqual(transport.request.await_count,1)
   second=await router.answer(m.history('A valid new goal',first));self.assertIn('Step 1 of 1',second)
   self.assertEqual(transport.request.await_count,2);self.assertEqual(r.calls,1)
   self.assertEqual(self.bus.snapshot()['state'],'stopped');self.assertEqual(router.actions.budget._used('music_control'),0)

if __name__=='__main__':unittest.main()
