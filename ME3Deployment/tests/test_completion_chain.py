"""Synthetic reproduction of recognized prefix loss across a canceled middle clip."""
import asyncio,base64,unittest
from tests.test_completion import Events
from me3_completion.controller import ConversationController
class ChainTests(unittest.IsolatedAsyncioTestCase):
    async def test_known_floor_survives_canceled_middle_recognition(self):
        floor=asyncio.Event();middle=asyncio.Event();answers=[];calls=[]
        class Client:
            async def call(self,role,g,**kw):
                if role=='tts':return {'pcm_b64':base64.b64encode(bytes(960)).decode()}
                calls.append((g,kw))
                if len(calls)==1:return {'text':'Let me explain what I need help with'}
                if len(calls)==2:middle.set();await asyncio.Event().wait()
                return {'text':'Please give me one short study tip.','previous_text':'I have several chapters to study'}
        class Provider:
            pending=None
            async def answer(self,history):answers.append(history);return 'Study the hardest topic first.'
        async def play(*_):pass
        async def cue(*_):floor.set()
        c=ConversationController(Client(),Provider(),Events(),play);c.listener_cue=cue
        try:
            c.submit(bytes(6400),c.interrupt());await asyncio.wait_for(floor.wait(),1)
            c.submit(bytes(6400),c.interrupt());await asyncio.wait_for(middle.wait(),1)
            c.submit(bytes(6400),c.interrupt());await c.task
            self.assertEqual(c.turns,1);self.assertEqual(c.asr_submissions,3);self.assertEqual(len(answers),1)
            self.assertIn('Let me explain what I need help with I have several chapters to study Please give me one short study tip.',str(answers))
        finally:await c.close()

    async def lost_chain(self,replacement):
        started=asyncio.Event();calls=0;answers=[];events=Events()
        class Client:
            async def call(self,role,g,**kw):
                nonlocal calls
                if role=='tts':return {'pcm_b64':base64.b64encode(bytes(960)).decode()}
                calls+=1
                if calls<3:started.set();await asyncio.Event().wait()
                return {'previous_text':'several unrecognized details','text':('Actually, what is two plus two?' if replacement else 'What is two plus two?')}
        class Provider:
            pending=None
            async def answer(self,history):answers.append(history);return 'Four.'
        async def play(*_):pass
        c=ConversationController(Client(),Provider(),events,play)
        try:
            for _ in range(2):
                started.clear();c.submit(bytes(6400),c.interrupt());await asyncio.wait_for(started.wait(),1)
            c.submit(bytes(6400),c.interrupt());await c.task
            if replacement:self.assertEqual(len(answers),1);self.assertIn('what is two plus two?',str(answers))
            else:self.assertFalse(answers);self.assertTrue(any(x['event']=='completion_unrecoverable_prefix' for x in events.rows))
        finally:await c.close()
    async def test_missing_older_raw_prefix_clarifies_instead_of_silently_dropping_it(self):await self.lost_chain(False)
    async def test_explicit_replacement_can_abandon_missing_older_prefix(self):await self.lost_chain(True)
