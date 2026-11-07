"""Direct spoken Stop -> real Router/controller/supervisor; no live providers/audio."""
import asyncio,contextlib,sqlite3,unittest
from types import SimpleNamespace
from unittest.mock import patch
from me3_assistant.voice_policy import ends_voice_session,bind_voice_session_stop
from me3_assistant.speech import AssistantController
from me3_assistant.gui import Console
from me3_assistant.registry import Registry
from me3_assistant.router import Router
from me3_assistant.actions import Journal
from speech_mcp.runtime import supervise
from tests import test_assistant_mode as f

class IntentTests(unittest.TestCase):
 def test_direct_session_end_covers_operator_words_and_polite_variants(self):
  for text in ['Stop.','please stop listening','stop listening to me','stop this conversation','Thank you, lets stop this conversation.','Thanks, let’s end our conversation now.','Could you please end the session?','let us stop the session please','finish this conversation','Goodbye.','Good bye','Bye','Bye bye','Thanks, goodbye','Goodbye for now']:
   with self.subTest(text=text):self.assertTrue(ends_voice_session(text))
 def test_negation_discussion_conditional_ack_and_targeted_task_stops_do_not_close_session(self):
  for text in [None,'',True,'do not stop listening',"don't stop",'What does stop listening mean?','When I say stop, stop listening.','Say stop','repeat stop listening','stop music','stop the player','stop playing','stop generating','stop talking','play Goodbye','say goodbye','do not say goodbye','before I say goodbye','what does goodbye mean','be quiet','cancel','okay','uh-huh','a'*257]:
   with self.subTest(text=text):self.assertFalse(ends_voice_session(text))

class StopTests(unittest.IsolatedAsyncioTestCase):
 asyncSetUp=f.MCPTests.asyncSetUp
 asyncTearDown=f.MCPTests.asyncTearDown
 cfg=f.MCPTests.cfg
 action_router=f.MCPTests.action_router
 async def controller(self,router,phrase='stop'):
  calls=[]
  class Speech:
   async def call(self,role,g,**args):
    calls.append(role)
    if role=='asr':return {'text':phrase}
    raise AssertionError('Stop must not dispatch synthesis')
  async def play(*a):raise AssertionError('Stop must not dispatch new playback')
  c=AssistantController(Speech(),router,self.events,play);router.controller=c;g=c.interrupt()
  return c,g,calls
 async def test_actual_recognized_turn_closes_supervised_owner_without_model_tts_or_extra_rpc(self):
  async with Registry(self.events,self.cfg()) as r:
   r.budget=f.FixtureBudget(0);r.journal=Journal(self.folder/'journal.jsonl');r.allowed_actions=[]
   model=f.Model();router=Router(model,r,self.events);c,g,calls=await self.controller(router)
   stop=asyncio.Event();bind_voice_session_stop(router,stop,self.events);started=asyncio.Event();end=asyncio.Event();order=[]
   class Runner:
    async def run(self):started.set();await end.wait();order.append('worker_closed')
    async def cancel(self):order.append('worker_cancel');end.set()
   class Transport:
    async def close(self):order.append('transport_closed')
   original=c.close
   async def close():await original();order.append('controller_closed')
   c.close=close
   owner=asyncio.create_task(supervise(Runner(),c,Transport(),self.events,asyncio.Event(),asyncio.Event(),30,stop_requested=stop))
   await started.wait();c.submit(bytes(6400),g);await asyncio.gather(c.task,return_exceptions=True)
   self.assertEqual(await asyncio.wait_for(owner,2),'caller_cancelled')
   self.assertTrue(stop.is_set());self.assertTrue(c.closed);self.assertEqual(calls,['asr']);self.assertEqual(model.seen,[]);self.assertEqual(r.calls,0)
   self.assertEqual(order,['controller_closed','worker_cancel','worker_closed','transport_closed'])
   self.assertFalse(any(e['event']=='turn_error' for e in self.events.rows));self.assertTrue(any(e['event']=='assistant_voice_stop_requested' for e in self.events.rows))
 async def test_actual_recognized_goodbye_closes_supervised_owner_without_model_tts_or_extra_rpc(self):
  async with Registry(self.events,self.cfg()) as r:
   r.budget=f.FixtureBudget(0);r.journal=Journal(self.folder/'journal.jsonl');r.allowed_actions=[]
   model=f.Model();router=Router(model,r,self.events);c,g,calls=await self.controller(router,'Goodbye')
   stop=asyncio.Event();bind_voice_session_stop(router,stop,self.events);started=asyncio.Event();end=asyncio.Event();order=[]
   class Runner:
    async def run(self):started.set();await end.wait();order.append('worker_closed')
    async def cancel(self):order.append('worker_cancel');end.set()
   class Transport:
    async def close(self):order.append('transport_closed')
   original=c.close
   async def close():await original();order.append('controller_closed')
   c.close=close
   owner=asyncio.create_task(supervise(Runner(),c,Transport(),self.events,asyncio.Event(),asyncio.Event(),30,stop_requested=stop))
   await started.wait();c.submit(bytes(6400),g);await asyncio.gather(c.task,return_exceptions=True)
   self.assertEqual(await asyncio.wait_for(owner,2),'caller_cancelled')
   self.assertTrue(stop.is_set());self.assertTrue(c.closed);self.assertEqual(calls,['asr']);self.assertEqual(model.seen,[]);self.assertEqual(r.calls,0)
   self.assertEqual(order,['controller_closed','worker_cancel','worker_closed','transport_closed'])
   self.assertFalse(any(e['event']=='turn_error' for e in self.events.rows));self.assertTrue(any(e['event']=='assistant_voice_stop_requested' for e in self.events.rows))
 async def test_stop_cancels_real_unconfirmed_MCP_proposal_without_dispatching_effect(self):
  async with self.action_router() as (router,r):
   await router.answer(f.history('Prepare three notebooks.'));operation=router.pending['operation_id'];calls=r.calls
   c,g,_=await self.controller(router);stop=asyncio.Event();bind_voice_session_stop(router,stop,self.events)
   try:
    with self.assertRaises(asyncio.CancelledError):await router.answer(f.history('stop this conversation'))
    self.assertTrue(stop.is_set());self.assertIsNone(router.pending);self.assertEqual(r.calls,calls);self.assertEqual(r.budget.used,0)
    self.assertEqual(r.journal.records[operation]['state'],'CANCELED')
   finally:await c.close()
 async def test_committed_real_MCP_SQLite_effect_and_verified_journal_are_not_undone_or_repeated(self):
  async with self.action_router() as (router,r):
   prompt=await router.answer(f.history('Save three notebooks.'))
   reply=await router.answer(f.history('confirm',prompt));self.assertIn('independently checked',reply)
   committed=dict(r.journal.records);calls=r.calls;c,g,_=await self.controller(router);stop=asyncio.Event();bind_voice_session_stop(router,stop,self.events)
   try:
    with self.assertRaises(asyncio.CancelledError):await router.answer(f.history('Thank you, lets stop this conversation.'))
    self.assertEqual(r.calls,calls);self.assertEqual(r.budget.used,1);self.assertEqual(r.journal.records,committed)
    with contextlib.closing(sqlite3.connect(self.folder/'test.sqlite3')) as db:self.assertEqual(db.execute('SELECT item,quantity FROM items').fetchall(),[('notebooks',3)])
   finally:await c.close()
 async def test_targeted_media_request_and_cancel_work_do_not_terminate_voice_session(self):
  async with Registry(self.events,self.cfg()) as r:
   r.budget=f.FixtureBudget(0);r.journal=Journal(self.folder/'journal.jsonl');r.allowed_actions=[]
   model=f.Model(f.decision(text='No music is playing.'));router=Router(model,r,self.events);c,g,_=await self.controller(router)
   stop=asyncio.Event();bind_voice_session_stop(router,stop,self.events)
   try:
    self.assertEqual(await router.answer(f.history('stop music')),'No music is playing.');self.assertFalse(stop.is_set());self.assertEqual(len(model.seen),1)
    await router.answer(f.history('cancel'));self.assertFalse(stop.is_set())
    await router.answer(f.history('stop generating'));self.assertFalse(stop.is_set());self.assertEqual(len(model.seen),1)
    self.assertEqual(await router.answer(f.history('don\u2019t worry')),'Okay.');self.assertEqual(len(model.seen),1);self.assertFalse(stop.is_set())
   finally:await c.close()
 async def test_stale_controller_cannot_signal_owner_stop(self):
  stop=asyncio.Event();router=SimpleNamespace(current=lambda g:False);bind_voice_session_stop(router,stop,self.events)
  self.assertFalse(router.request_voice_session_stop(7));self.assertFalse(stop.is_set())

class GUIStopTests(unittest.IsolatedAsyncioTestCase):
 async def test_gui_cannot_return_to_listening_during_owner_cleanup_and_ends_stopped(self):
  c=Console(f.config(),{'revision':'fixture'});c.mode='voice';c.stop_event=asyncio.Event();started=asyncio.Event();closing=asyncio.Event();release=asyncio.Event()
  async def run(*a,stop_requested,**kw):started.set();await stop_requested.wait();closing.set();await release.wait()
  with patch('me3_assistant.runner.run',run):
   c.task=asyncio.create_task(c.voice());await started.wait()
   router=SimpleNamespace(current=lambda g:True);bind_voice_session_stop(router,c.stop_event,SimpleNamespace(emit=c.observe));router.request_voice_session_stop(1);await closing.wait()
   for event,fields in [('core_turn_settled',{}),('completion_ownership',{'current':'listening'}),('generation_playout_started',{})]:
    c.observe(event,**fields);self.assertEqual(c.state,'stopping')
   release.set();await asyncio.wait_for(c.task,1)
   self.assertEqual(c.state,'stopped');self.assertIsNone(c.public_error);self.assertIn('microphone closed',c.notice)
