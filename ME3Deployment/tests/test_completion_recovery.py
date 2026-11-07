"""Regression of the observed turn-budget defect; synthetic recognition, not live evidence."""
import asyncio,base64,unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from me3_completion.policy import resolve_segments
from me3_completion.controller import ConversationController
from me3_completion.endpoint import LocalEndpoint
from tests.test_completion import Events,Analyzer
class PolicyRecoveryTests(unittest.TestCase):
    def test_explicit_floor_waits_until_actual_request(self):
        first='Let me explain what I need help with';middle='I have several chapters to study'
        self.assertEqual(resolve_segments(None,first),('incomplete',first))
        state,joined=resolve_segments(first,middle);self.assertEqual(state,'incomplete');self.assertEqual(joined,first+' '+middle)
        state,complete=resolve_segments(joined,'Please give me one short study tip.');self.assertEqual(state,'continuation');self.assertTrue(complete.startswith(first+' '+middle))
    def test_replacement_and_confirmation_do_not_join_held_task(self):
        old='Let me explain what I need help with'
        self.assertEqual(resolve_segments(old,'Actually, what is five plus five?'),('replacement','what is five plus five?'))
        self.assertEqual(resolve_segments(old,'Confirm'),('ambiguous',None))
    def test_narrative_context_joins_only_explicit_request_continuation(self):
        self.assertEqual(resolve_segments('I have an exam tomorrow','give me one short study tip')[0],'continuation')
        self.assertEqual(resolve_segments('unrelated finished topic','some ambiguous fragment')[0],'ambiguous')
class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    def make(self,mode):
        e=Events();self.asr=0;self.answers=[];self.started=asyncio.Event()
        outer=self
        class Client:
            async def call(self,role,g,**kw):
                if role=='tts':return {'pcm_b64':base64.b64encode(bytes(960)).decode()}
                outer.asr+=1;outer.started.set()
                if mode=='cancel' and outer.asr==1:await asyncio.Event().wait()
                if mode=='floor':return {'text':'Let me explain what I need help with' if outer.asr==1 else 'Please give me one short study tip.'}
                return {'text':'of France?','previous_text':'What is the capital'}
        class Provider:
            pending=None
            async def answer(self,history):outer.answers.append(history);return 'One clear reply.'
        async def play(*args):pass
        c=ConversationController(Client(),Provider(),e,play);return c,e
    async def test_cancelled_prefix_continuation_is_one_turn_but_two_counted_submissions(self):
        c,e=self.make('cancel');c.submit(bytes(6400),c.interrupt());await self.started.wait();old=c.task
        c.submit(bytes(6400),c.interrupt());await c.task
        self.assertEqual(c.turns,1);self.assertEqual(c.asr_submissions,2);self.assertEqual(self.asr,2);self.assertTrue(old.cancelled());self.assertEqual(len(self.answers),1)
        self.assertIn('What is the capital of France?',str(self.answers));await c.close()
    async def test_recognized_floor_cue_preserves_prefix_and_never_answers_incomplete_instruction(self):
        c,e=self.make('floor');cue=AsyncMock();c.listener_cue=cue;c.submit(bytes(6400),c.interrupt())
        async with asyncio.timeout(1):
            while cue.await_count==0:await asyncio.sleep(.001)
        self.assertFalse(self.answers);c.submit(bytes(6400),c.interrupt());await c.task
        self.assertEqual(c.turns,1);self.assertEqual(self.asr,2);self.assertEqual(cue.await_count,1);self.assertEqual(len(self.answers),1)
        self.assertIn('Let me explain what I need help with Please give me one short study tip.',str(self.answers));await c.close()
    async def test_continuation_attempt_cap_clarifies_instead_of_unbounded_dispatch(self):
        c,e=self.make('cancel')
        async def wait(*args):await asyncio.Event().wait()
        c.turn=wait
        for _ in range(4):c.submit(bytes(6400),c.interrupt());await asyncio.sleep(0)
        c.submit(bytes(6400),c.interrupt());await c.task
        self.assertEqual(c.turns,1);self.assertEqual(c.asr_submissions,4);self.assertEqual(self.asr,0);self.assertTrue(any(x['event']=='completion_segment_limit' for x in e.rows));await c.close()
    async def test_marginal_neural_complete_score_holds_with_bounded_policy(self):
        a=Analyzer();a.release.set()
        async def marginal():return SimpleNamespace(name='COMPLETE'),SimpleNamespace(probability=.5295)
        a.analyze_end_of_turn=marginal;e=LocalEndpoint(Events(),a)
        self.assertFalse(await e.decide(bytes(6400),1));await e.close()
    async def test_strong_neural_complete_score_can_endpoint_without_extra_hold(self):
        a=Analyzer();a.release.set()
        async def strong():return SimpleNamespace(name='COMPLETE'),SimpleNamespace(probability=.99)
        a.analyze_end_of_turn=strong;e=LocalEndpoint(Events(),a)
        self.assertTrue(await e.decide(bytes(6400),1));await e.close()
