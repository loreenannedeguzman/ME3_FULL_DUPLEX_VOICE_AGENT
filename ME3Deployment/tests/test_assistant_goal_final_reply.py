import asyncio,json,unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from google.genai import types
from me3_assistant.router import Router
from me3_assistant.actions import Journal
from me3_assistant.gui import Console
from me3_assistant.reasoning import Reasoning
from me3_assistant.groq_json import json_messages,final_speech_messages
from tests import test_assistant_mode as f
from tests import test_assistant_workflow as w
from tests import test_assistant_groq_adapter as a

class CompletedGoalTests(unittest.IsolatedAsyncioTestCase):
 asyncSetUp=f.MCPTests.asyncSetUp
 asyncTearDown=f.MCPTests.asyncTearDown
 cfg=f.MCPTests.cfg
 def prepare(self,r):r.budget=f.FixtureBudget(0);r.journal=Journal(self.folder/'journal.jsonl');r.allowed_actions=[]
 async def test_observed_repeat_pattern_gets_one_answer_only_pass_and_one_actual_read(self):
  async with f.Registry(self.events,self.cfg()) as r:
   self.prepare(r);seen=[]
   class Model:
    async def decide(self,history,declarations,continuation):
     seen.append(declarations)
     if not continuation or declarations:return w.plan(('configured__calculate_expression',{'expression':'250*.18/3'}))
     return f.decision(text='Gravity pulls droplets down; the result is fifteen.')
   reply=await Router(Model(),r,self.events).answer(f.history('Explain the falling droplets and calculate the requested amount.'))
   self.assertIn('Gravity',reply);self.assertIn('fifteen',reply);self.assertEqual(r.calls,1);self.assertEqual(len(seen),2);self.assertEqual(seen[-1],[]);self.assertEqual(r.journal.records,{})
 async def test_ignoring_answer_only_boundary_cannot_repeat_rpcs_or_consume_another_round(self):
  async with f.Registry(self.events,self.cfg()) as r:
   self.prepare(r);plan=w.plan(('configured__calculate_expression',{'expression':'2+2'}));model=f.Model(plan,plan,plan)
   reply=await Router(model,r,self.events).answer(f.history('Calculate then explain.'))
   self.assertIn('could not produce',reply);self.assertEqual(r.calls,1);self.assertEqual(len(model.seen),2);self.assertTrue(any(e['event']=='assistant_goal_summary_rejected' for e in self.events.rows))
 async def test_new_user_request_restores_tool_selection_after_completed_summary(self):
  async with f.Registry(self.events,self.cfg()) as r:
   self.prepare(r);model=f.Model(w.plan(('configured__calculate_expression',{'expression':'2+2'})),f.decision(text='Four.'),f.decision(text='A fresh topic.'));router=Router(model,r,self.events)
   first=await router.answer(f.history('Calculate two plus two.'));self.assertEqual(model.seen[1][1],[])
   self.assertEqual(await router.answer(f.history('Change topics.',first)),'A fresh topic.');self.assertTrue(model.seen[2][1]);self.assertEqual(r.calls,1)

class FinalWireTests(unittest.IsolatedAsyncioTestCase):
 async def test_google_answer_only_pass_omits_empty_function_tool_container_without_network(self):
  model=Reasoning.__new__(Reasoning);model.events=a.Events();model.lock=asyncio.Lock();model.thinking_profile='low'
  call=AsyncMock(return_value=NS(candidates=[NS(content=types.Content(role='model',parts=[types.Part(text='A final reply.')]))]))
  model.traced=NS(provider=NS(client=NS(aio=NS(models=NS(generate_content=call)))))
  self.assertEqual((await model.decide([],[],[]))[2],'A final reply.');self.assertIsNone(call.call_args.kwargs['config'].tools);self.assertFalse(model.lock.locked())
 def test_completed_phase_is_explicit_in_json_input_with_no_available_operations(self):
  continuation=[types.Content(role='user',parts=[types.Part(text='Actual operation result data.')])]
  wire=final_speech_messages(f.history('Answer both parts.'),continuation);p=json.loads(wire[1]['content'])
  self.assertEqual(p['phase'],'final_reply');self.assertEqual(p['permitted_operations'],[]);self.assertIn('calls MUST be an empty array',wire[0]['content']);self.assertIn('Answer both parts.',str(p['request_to_complete']))

class VisibleFailureTests(unittest.IsolatedAsyncioTestCase):
 def console(self):
  c=Console(f.config('extension'),{'revision':'fixture'});c.mode='text';c.state='ready';c.ready=asyncio.Event();c.ready.set();c.stop_event=asyncio.Event();return c
 async def test_failed_request_displays_truthful_safe_reply_and_next_explicit_request_can_recover(self):
  c=self.console();c.router=NS(answer=AsyncMock(side_effect=RuntimeError('PRIVATE_SERVER_ERROR')),pending=None)
  await c.command({'action':'send','text':'A request.'});await c.turn
  self.assertEqual(c.state,'awaiting_display');self.assertIn('could not finish',c.pending['text']);self.assertNotIn('PRIVATE',str(c.pending)+str(c.events))
  await c.command({'action':'ack','id':c.pending['id']});c.router.answer.side_effect=None;c.router.answer.return_value='A new reply.'
  await c.command({'action':'send','text':'A replacement request.'});await c.turn;self.assertEqual(c.pending['text'],'A new reply.');self.assertEqual(c.router.answer.await_count,2)
 async def test_stale_or_stopped_failure_does_not_publish_obsolete_error(self):
  for stopped in [False,True]:
   c=self.console();c.generation=2 if not stopped else 1
   if stopped:c.stop_event.set()
   c.router=NS(answer=AsyncMock(side_effect=RuntimeError('PRIVATE')),pending=None)
   await c.answer(1);self.assertIsNone(c.pending)

if __name__=='__main__':unittest.main()
