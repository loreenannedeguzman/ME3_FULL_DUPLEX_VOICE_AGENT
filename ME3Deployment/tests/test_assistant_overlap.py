"""Provider-free replay of the failed acknowledgment surface and safety cases."""
import unittest
from me3_assistant.overlap import acknowledgment_surface,attach
from me3_completion.policy import listener_ack
from tests import test_agent as fixtures

class SurfaceTests(unittest.TestCase):
    def test_same_word_with_ASR_boundary_punctuation(self):
        for text in ('¿Okay?','“Okay.”','"Okay"','Okay,','(okay)','¡OK!'):
            with self.subTest(text=text):
                self.assertTrue(listener_ack(acknowledgment_surface(text),.48))
    def test_never_erases_words_or_turns_commands_into_acknowledgments(self):
        for text in ('not okay','okay stop','yes','confirm','cancel','stop','no','okay-ish','okay/stop','o.k.'):
            with self.subTest(text=text):
                self.assertEqual(acknowledgment_surface(text),text)
                self.assertFalse(listener_ack(acknowledgment_surface(text),.48))
    def test_duration_and_pending_action_rules_still_apply(self):
        for duration in (1,1.2):self.assertFalse(listener_ack(acknowledgment_surface('¿Okay?'),duration))
        self.assertFalse(listener_ack(acknowledgment_surface('¿Okay?'),.48,task_pending=True))

class NormalOverlapTests(fixtures.OverlapTests):
    def setup_processor(self):
        events=super().setup_processor();attach(self.p,self.client,self.provider,events);return events
    async def test_failed_live_surface_resumes_old_generation_with_one_ASR_call(self):
        self.setup_processor();self.text='¿Okay?';calls=[];original=self.client.call
        async def call(*a,**kw):calls.append(a);return await original(*a,**kw)
        self.client.call=call
        await self.p.classify_overlap(1,b'audio',.48,3,self.old_task)
        self.assertTrue(self.resumed);self.assertEqual(self.g,3)
        self.assertEqual(len(calls),1);self.assertFalse(any(x[0] in {'takeover','submit'} for x in self.calls))
        self.assertTrue(any(x['event']=='assistant_ack_surface_normalized' for x in self.rows))
    async def test_pending_confirmation_preserves_original_recognition_and_never_resumes(self):
        self.setup_processor();self.text='¿Okay?';self.provider.pending={'phase':'confirm'}
        await self.p.classify_overlap(1,b'audio',.48,3,self.old_task)
        self.assertFalse(self.resumed);self.assertEqual(self.calls[-1],('submit',4,self.text))

if __name__=='__main__':unittest.main()
