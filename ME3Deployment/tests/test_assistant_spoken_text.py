import unittest
from me3_assistant.spoken_text import spoken_text
from me3_assistant.speech import AssistantController
from tests import test_assistant_speech as fixture

class SurfaceTests(unittest.TestCase):
 def test_explicit_math_and_acronyms_preserve_meaning(self):
  self.assertEqual(spoken_text('a² + b² = c². PDF and AI API.'),'a squared + b squared = c squared. P D F and A I A P I.')
  self.assertEqual(spoken_text('x^2, y^{3}, z^10, 2.5².'),'x squared, y cubed, z to the power of 10, 2.5 squared.')
 def test_URLs_files_plain_numbers_names_and_variable_identifiers_stay_intact(self):
  text='Read book.pdf at https://example.org/AI/x^2.pdf and /home/user/AI.pdf. A2 is distinct from A squared; 250 x 3 = 750. Forestella and trigonometry.'
  self.assertEqual(spoken_text(text),text)

class ControllerSurfaceTests(unittest.IsolatedAsyncioTestCase):
 async def test_actual_controller_speaks_notation_words_but_projects_original_text(self):
  client=fixture.Speech(8);shown=[]
  async def play(pcm,g):pass
  c=AssistantController(client,None,fixture.Events(),play);g=c.clock.advance();c.history=[{'type':'user_input','content':[{'type':'text','text':'Explain notation.'}]}]
  c.display_reply=lambda g,t,s:shown.append((t,s))
  try:
   text='a² + b² = c². Read the PDF.';self.assertTrue(await c.speak(text,g,'normal'))
   self.assertEqual(client.calls[0][1],'a squared + b squared = c squared. Read the P D F.')
   self.assertEqual(shown,[(text,'preparing'),(text,'complete')]);self.assertEqual(len(c.history),1)
  finally:await c.close()
