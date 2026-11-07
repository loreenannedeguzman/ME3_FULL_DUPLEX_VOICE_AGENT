import asyncio,base64,time,unittest
from types import SimpleNamespace
from me3_agent.segments import resolve_segments
from me3_agent.controller import AgentController

class SegmentTests(unittest.TestCase):
    def test_varied_questions_after_explicit_floor_hold_preserve_actual_words(self):
        prefix='Let me explain what I am asking.'
        for question in ['Why does the Moon appear bright?','Who was Marie Curie?','When does autumn begin?','What makes leaves green?','Which planet has rings?','How long should I walk?','What time is it in Manila?']:
            with self.subTest(question=question):self.assertEqual(resolve_segments(prefix,question),('continuation',prefix+' '+question))
    def test_confirmation_replacement_and_ambiguous_fragment_are_not_guessed(self):
        self.assertEqual(resolve_segments('Let me think','Confirm'),('ambiguous',None))
        self.assertEqual(resolve_segments('Let me think','Instead, what is two plus two?'),('replacement','what is two plus two?'))
        self.assertEqual(resolve_segments('Let me think','because I am worried')[0],'incomplete')
        self.assertEqual(resolve_segments('Let me think','Why does')[0],'incomplete')
        self.assertEqual(resolve_segments(None,'Let me explain what I am asking.'),('incomplete','Let me explain what I am asking.'))
    def test_question_after_floor_hold_in_same_clip_is_complete(self):
        text='Let me explain what I am asking. Why does the Moon appear bright?'
        self.assertEqual(resolve_segments(None,text),('complete',text))

class IntegratedSegmentTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_controller_continuation_reaches_reasoning_not_repeat_notice(self):
        class Clients:
            async def call(self,role,g,**kw):return {'text':'Why does the Moon appear bright?','request_id':'fixture','generation':g}
        e=SimpleNamespace(emit=lambda *a,**kw:None)
        c=AgentController(Clients(),SimpleNamespace(pending=None),e,None)
        g=c.interrupt();c.slot={'generation':g,'pcm':bytes(6400),'stage':'asr','submitted':time.monotonic(),'candidate':{'text':'Let me explain what I am asking.','submitted':time.monotonic(),'generation':g-1}}
        data=await c.recognize(g,pcm_b64=base64.b64encode(bytes(6400)).decode(),sample_rate=16000)
        self.assertEqual(data['text'],'Let me explain what I am asking. Why does the Moon appear bright?')
        self.assertEqual(c.slot['stage'],'reasoning');await c.close()
