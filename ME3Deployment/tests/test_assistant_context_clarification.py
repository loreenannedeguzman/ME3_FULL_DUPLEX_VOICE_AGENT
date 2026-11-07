"""Provider/device-free natural follow-ups and clarification/display ownership."""
import asyncio,time,unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from me3_assistant.speech import AssistantController
from me3_assistant.gui import Console,row
from tests.test_assistant_speech import Speech,Events
from tests.test_assistant_mode import config

class Client(Speech):
 def __init__(self,text):super().__init__(8);self.text=text;self.asr_calls=0
 async def call(self,role,generation,**args):
  if role=='asr':
   self.asr_calls+=1
   return dict(text=self.text,handled=True,generation=generation,request_id='fixture-asr')
  return await super().call(role,generation,**args)

class ContextTests(unittest.IsolatedAsyncioTestCase):
 def setup_controller(self,text,previous=None,play=None):
  async def played(pcm,g):pass
  events=Events();client=Client(text);provider=NS(answer=AsyncMock(return_value='Use the earlier formula with the new radius.'),pending=None)
  c=AssistantController(client,provider,events,play or played);c.clock.advance()
  c.history=[row('user','Give me the circle area formula.'),row('assistant','The area is pi times r squared.')]
  c.slot=dict(generation=1,stage='asr',pcm=bytes(640),submitted=time.monotonic(),candidate={'text':previous,'submitted':time.monotonic()} if previous else None)
  gui=Console(config(),{'revision':'fixture'});gui.bind_controller(c)
  return c,client,provider,events,gui
 async def test_contextual_followup_reaches_reasoning_once_with_formula_and_exact_words(self):
  for text in ['and if r is 10, what is the area?', 'with that radius, compare the area to the earlier one.', 'of those choices, which fits my earlier constraints?']:
   c,client,p,e,gui=self.setup_controller(text)
   try:
    await c.turn(bytes(640),1)
    self.assertEqual(client.asr_calls,1);p.answer.assert_awaited_once()
    history=p.answer.call_args.args[0]
    self.assertEqual(history[-1]['content'][0]['text'],text)
    self.assertIn('pi times r squared',history[1]['content'][0]['text'])
    self.assertFalse(any(r['event'] in {'empty_transcription','assistant_clarification_delivered','turn_error'} for r in e.rows))
    self.assertIn('The area is pi times r squared.',[m['text'] for m in gui.display_conversation(c.history)])
   finally:await c.close()
 async def test_real_retained_fragment_is_clarified_without_overwriting_old_formula_or_false_empty(self):
  c,client,p,e,gui=self.setup_controller('and this one','An unfinished earlier fragment')
  try:
   await c.turn(bytes(640),1);p.answer.assert_not_awaited()
   shown=gui.display_conversation(c.history)
   self.assertEqual([m['text'] for m in shown],['Give me the circle area formula.','The area is pi times r squared.','and this one',"Could you clarify what you'd like me to do?"])
   self.assertEqual(sum(r['event']=='assistant_recognition_handled' for r in e.rows),1)
   self.assertEqual(sum(r['event']=='assistant_clarification_delivered' for r in e.rows),1)
   self.assertFalse(any(r['event']=='empty_transcription' for r in e.rows));self.assertFalse(c.consume_handled_recognition(1))
   self.assertEqual(client.asr_calls,1);self.assertIsNone(p.pending)
  finally:await c.close()
 async def test_genuine_empty_ASR_cannot_use_payload_flag_to_suppress_failure_or_replace_formula(self):
  c,client,p,e,gui=self.setup_controller('')
  try:
   await c.turn(bytes(640),1);p.answer.assert_not_awaited()
   self.assertEqual(sum(r['event']=='empty_transcription' for r in e.rows),1)
   self.assertFalse(any(r['event']=='assistant_recognition_handled' for r in e.rows))
   self.assertEqual([m['text'] for m in gui.display_conversation(c.history)],['Give me the circle area formula.','The area is pi times r squared.'])
  finally:await c.close()
 async def test_interrupted_clarification_does_not_claim_delivered_or_reuse_previous_reply_anchor(self):
  entered=asyncio.Event()
  async def blocked(pcm,g):entered.set();await asyncio.Future()
  c,client,p,e,gui=self.setup_controller('and this one','Unfinished earlier fragment',blocked)
  c.spawn(c.turn(bytes(640),1));await asyncio.wait_for(entered.wait(),1);task=c.task
  self.assertIn('The area is pi times r squared.',[m['text'] for m in gui.display_conversation(c.history)])
  c.interrupt();await asyncio.gather(task,return_exceptions=True)
  self.assertFalse(any(r['event']=='assistant_clarification_delivered' for r in e.rows));self.assertFalse(c.consume_handled_recognition(1))
  self.assertIn('The area is pi times r squared.',[m['text'] for m in gui.display_conversation(c.history)])
  p.answer.assert_not_awaited();await c.close()

