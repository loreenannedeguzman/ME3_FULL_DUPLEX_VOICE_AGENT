"""Long prose preparation/status and cancellation through the real controller."""
import asyncio,re,unittest
from me3_assistant.speech import response_parts,AssistantController
from me3_assistant.gui import Console
from tests import test_assistant_speech as f
from tests.test_assistant_mode import config

LONG=('Here are useful study ideas for your next reading session. '
      'First, explain each idea in your own words and compare it with what you already know. '
      'Second, work through a fresh problem without looking at the solution, then inspect any mistake. '
      'Third, return to the difficult material after a break and decide what to practice next. ')*2

class ProseTests(unittest.TestCase):
 def test_every_byte_and_quantity_survives_short_bounded_prose_parts(self):
  text=LONG+' Keep 3.14, 18.5 percent, 250 x 3 = 750 and 2000 unchanged.'
  parts=response_parts(text);self.assertEqual(''.join(parts),text);self.assertGreater(len(parts),2)
  self.assertTrue(all(0<len(p)<=160 for p in parts));self.assertTrue(all(not p.endswith('3.') for p in parts))
  self.assertEqual(''.join(re.findall(r'\S+',text)),''.join(re.findall(r'\S+',''.join(parts))))

class ControllerTests(unittest.IsolatedAsyncioTestCase):
 def make(self):
  gui=Console(config(),{'revision':'fixture'});gui.mode='voice'
  class Events(f.Events):
   def emit(self,event,**fields):super().emit(event,**fields);gui.observe(event,**fields)
  speech=f.Speech(100);speech.hold=True;played=[]
  async def play(pcm,g):played.append(g)
  c=AssistantController(speech,None,Events(),play);c.clock.advance();c.history=[{'type':'user_input','content':[{'type':'text','text':'Explain useful reading approaches.'}]}];c.pending_history_generation=1;gui.bind_controller(c)
  return gui,speech,played,c
 async def test_held_continuation_reports_preparing_voice_until_all_parts_are_submitted(self):
  gui,speech,played,c=self.make();work=asyncio.create_task(c.speak(LONG,1,'normal'))
  try:
   await asyncio.wait_for(speech.entered.wait(),1)
   self.assertEqual(gui.state,'preparing_speech');self.assertEqual(played,[1]);self.assertFalse(work.done())
   self.assertLessEqual(len(speech.calls[1][1]),160);self.assertEqual(len(speech.calls),2)
   self.assertEqual(gui.voice_replies[-1]['state'],'preparing');self.assertFalse(any(LONG in str(r) for r in c.history))
   speech.wait.set();self.assertTrue(await work)
   self.assertEqual(''.join(v[1] for v in speech.calls),LONG);self.assertEqual(len(played),len(response_parts(LONG)))
   self.assertEqual(gui.voice_replies[-1]['state'],'complete');self.assertFalse(any(r.get('partial_output') for r in c.history))
  finally:
   work.cancel();await asyncio.gather(work,return_exceptions=True);await c.close()
 async def test_replacement_cancels_the_single_lookahead_and_never_plays_late_long_reply(self):
  gui,speech,played,c=self.make();speech.ignore_cancel=True;c.spawn(c.speak(LONG,1,'normal'));old=c.task
  try:
   await asyncio.wait_for(speech.entered.wait(),1);c.interrupt();await asyncio.gather(old,return_exceptions=True)
   self.assertEqual(played,[1]);self.assertEqual(len(speech.calls),2);self.assertTrue(speech.cancelled)
   self.assertEqual(gui.voice_replies[-1]['state'],'interrupted')
   partial=next(r for r in c.history if r.get('partial_output'));self.assertEqual(partial['content'][0]['text'],response_parts(LONG)[0])
   self.assertNotIn(LONG,str(c.history))
  finally:await c.close()

