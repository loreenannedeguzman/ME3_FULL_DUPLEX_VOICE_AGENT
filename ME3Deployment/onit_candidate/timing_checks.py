"""Provider-free coverage of native planning and voice deadline ownership."""
import asyncio,copy,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace
from task_mcp.client import ROOT
from onit_candidate.spoken import CandidateSpeech
from onit_candidate.conversation import Conversation,Context,controller_class
from onit_candidate.checks import Events,Budget,FixtureRegistry,FixtureTransport,response,native
from onit_candidate.engine import Engine
from tests.test_assistant_speech import Speech

class PlanningChecks(unittest.IsolatedAsyncioTestCase):
 def control(self):
  async def play(pcm,g):pass
  c=CandidateSpeech(Speech(30),None,Events(),play);c.clock.advance();return c
 async def test_valid_multistep_planning_outlives_old_voice_window_then_restores_only_remaining_voice_time(self):
  c=self.control()
  try:
   async with asyncio.timeout(.05) as timer:
    c.deadlines[1]={'timer':timer,'audio_credited':False}
    async with c.planning_scope(1,.5):await asyncio.sleep(.08)
    self.assertLess(timer.when()-asyncio.get_running_loop().time(),.051);self.assertGreater(timer.when()-asyncio.get_running_loop().time(),.025)
   self.assertTrue(any(e['event']=='onit_voice_planning_deadline_exit' and e['voice_allowance_restored'] for e in c.events.rows))
  finally:await c.close()
 async def test_planning_itself_keeps_a_finite_hard_deadline(self):
  c=self.control()
  try:
   with self.assertRaises(TimeoutError):
    async with asyncio.timeout(1) as timer:
     c.deadlines[1]={'timer':timer,'audio_credited':False}
     async with c.planning_scope(1,.03):await asyncio.Event().wait()
   self.assertTrue(timer.expired());self.assertFalse(c.events.rows[-1]['voice_allowance_restored'])
  finally:await c.close()
 async def test_capacity_wait_does_not_extend_native_absolute_task_bound(self):
  c=self.control()
  try:
   async with asyncio.timeout(1) as timer:
    c.deadlines[1]={'timer':timer,'audio_credited':False}
    async with c.planning_scope(1,2):
     before=timer.when();c.credit_provider_wait(1,1);self.assertEqual(timer.when(),before)
   self.assertEqual(c.deadlines[1]['provider_wait_credited'],1)
  finally:await c.close()
 async def test_stale_generation_cannot_restore_or_transfer_deadline(self):
  c=self.control()
  try:
   async with asyncio.timeout(1) as timer:
    c.deadlines[1]={'timer':timer,'audio_credited':False}
    async with c.planning_scope(1,2):c.clock.advance();old=timer.when()
    self.assertEqual(timer.when(),old);self.assertFalse(c.events.rows[-1]['voice_allowance_restored'])
  finally:await c.close()
 async def test_outer_session_cancellation_still_wins_inside_planning(self):
  c=self.control()
  try:
   with self.assertRaises(TimeoutError):
    async with asyncio.timeout(.03):
     async with asyncio.timeout(1) as timer:
      c.deadlines[1]={'timer':timer,'audio_credited':False}
      async with c.planning_scope(1,2):await asyncio.Event().wait()
  finally:await c.close()
 async def test_voice_processing_stall_still_expires_after_planning_returns(self):
  c=self.control()
  try:
   with self.assertRaises(TimeoutError):
    async with asyncio.timeout(.04) as timer:
     c.deadlines[1]={'timer':timer,'audio_credited':False}
     async with c.planning_scope(1,.5):await asyncio.sleep(.06)
     await asyncio.Event().wait()
  finally:await c.close()
 async def test_invalid_limits_nested_scope_and_absent_owner_rejected(self):
  c=self.control()
  try:
   for limit in [True,0,-1,3601,float('inf')]:
    with self.assertRaises(ValueError):
     async with c.planning_scope(1,limit):pass
   with self.assertRaises(asyncio.CancelledError):
    async with c.planning_scope(1,1):pass
   async with asyncio.timeout(1) as timer:
    c.deadlines[1]={'timer':timer,'audio_credited':False}
    async with c.planning_scope(1,1):
     with self.assertRaises(ValueError):
      async with c.planning_scope(1,1):pass
  finally:await c.close()
 async def test_actual_Onit_shared_voice_owner_completes_sequential_tools_beyond_scaled_old_cutoff(self):
  class SlowTransport(FixtureTransport):
   async def request(self,*a,**kw):await asyncio.sleep(.08);return await super().request(*a,**kw)
  first=native(args={'value':7});second=native(args={'value':8});second['id']='fixture_second'
  registry=FixtureRegistry();events=Events();budget=Budget();budget.remaining=lambda role:100;transport=SlowTransport(response(calls=[first]),response(calls=[second]),response('The two inspected fixture values are seven and eight.'))
  with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='onit-task-deadline-') as tmp:
   folder=Path(tmp);events.path=folder/'events.jsonl';engine=Engine(ROOT,registry,transport,budget,events);context=Context();c=Conversation(ROOT,registry,transport,budget,events,context,engine=engine,voice=True)
   async def play(pcm,g):pass
   speech=Speech(30);control=controller_class()(speech,c,events,play);control.shared_context=context;c.controller=control;control.turn_deadline=.15
   try:
    await control.typed_turn('Inspect both fixture values.',control.interrupt())
    self.assertEqual(context.history[-1]['content'][0]['text'],'The two inspected fixture values are seven and eight.',str(events.rows));self.assertEqual((registry.calls,budget.used,transport.calls),(2,3,3));self.assertFalse(any(e['event'] in {'turn_error','core_turn_deadline'} for e in events.rows));self.assertTrue(any(e['event']=='response_complete' for e in events.rows))
   finally:await control.close();await c.close()
