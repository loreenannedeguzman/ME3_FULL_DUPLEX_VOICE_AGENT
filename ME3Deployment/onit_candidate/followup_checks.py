"""Regression coverage for the observed 4141-character follow-up and speech surface.
Fake provider/PCM only, never network requests, real synthesis, or live audio.
"""
import asyncio,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from onit_candidate.presentation import spoken_text,speech_parts
from onit_candidate.protocol import MAX_RESPONSE_CHARS,CandidateError,validate_response
from onit_candidate.spoken import CandidateSpeech
from onit_candidate.engine import Engine
from onit_candidate.conversation import Conversation,Context,controller_class
from onit_candidate.checks import FixtureTransport,FixtureRegistry,response,Budget,Events
from tests.test_assistant_speech import Speech,NumericSpeech,TEXT
from onit_candidate.voice_checks import EXPLANATION,FakeEngine
from task_mcp.client import ROOT

# Unique words avoid the pinned upstream loop's intentional repetition trimming.
LONG=' '.join('detail'+str(n).zfill(4) for n in range(414))+' conclusion.'

class PresentationChecks(unittest.TestCase):
    def test_markdown_headings_lists_and_links_are_speech_only(self):
        source='## AI tasks\n\n- **Decision-making**: choose an action.\n- __Learning__: improve.\nSee [Wikipedia](https://en.wikipedia.org/wiki/AI).'
        projected=spoken_text(source)
        self.assertEqual(projected,'artificial intelligence tasks\n\nDecision-making: choose an action.\nLearning: improve.\nSee Wikipedia.')
        self.assertIn('**Decision-making**',source)
    def test_initialisms_are_explicit_and_not_substrings_or_literal_filenames(self):
        self.assertEqual(spoken_text('AI, API, PDF; Aileen; my_AI.pdf.'),'artificial intelligence, ay, pee, eye, pee, dee, eff; Aileen; my_AI.pdf.')
        self.assertEqual(spoken_text(spoken_text('AI and PDF.')),spoken_text('AI and PDF.'))
    def test_math_and_literal_code_paths_remain_distinct_from_emphasis(self):
        value='**Area**: 2 * 3; a * b * c; x² + y^2. `a_b ** AI` C:\\AI\\file_name.pdf https://example.org/AI?q=a_b'
        self.assertEqual(spoken_text(value),'Area: 2 * 3; a * b * c; x squared + y squared. a_b ** AI C:\\AI\\file_name.pdf https://example.org/AI?q=a_b')
    def test_fenced_code_removes_only_enclosing_markers(self):
        self.assertEqual(spoken_text('```python\nx = 2 * 3 # AI\n```'),'x = 2 * 3 # AI\n')
    def test_every_synthesis_part_is_bounded_without_losing_any_text(self):
        for text in [LONG,'x'*4000,'First sentence.\n'+'z'*900,EXPLANATION]:
            parts=speech_parts(text)
            self.assertEqual(''.join(parts),text);self.assertTrue(all(0<len(p)<=160 for p in parts))
    def test_one_protocol_bound_retains_incomplete_blank_and_oversize_rejections(self):
        validate_response(response('z'*MAX_RESPONSE_CHARS))
        for raw in [response('z'*(MAX_RESPONSE_CHARS+1)),response(' ')]:
            with self.assertRaises(CandidateError):validate_response(raw)
        raw=response(LONG);raw['choices'][0]['finish_reason']='length'
        with self.assertRaisesRegex(CandidateError,'incomplete_response'):validate_response(raw)

class SpeechChecks(unittest.IsolatedAsyncioTestCase):
    def control(self,speech,play):
        c=CandidateSpeech(speech,None,Events(),play);c.clock.advance();return c
    async def test_complete_segmented_pcm_above_120_seconds_uses_only_actual_time_and_one_grace(self):
        speech=Speech(100);speech.seconds=20;played=[]
        async def play(pcm,g):played.append(len(pcm)/48000)
        c=self.control(speech,play)
        async with asyncio.timeout(55) as timer:
            initial=timer.when();c.deadlines[1]={'timer':timer,'audio_credited':False}
            self.assertTrue(await c.speak(EXPLANATION,1,'normal'))
            self.assertGreater(sum(played),120)
            self.assertAlmostEqual(timer.when()-initial,sum(played)+10,places=4)
        self.assertEqual(''.join(x[1] for x in speech.calls),EXPLANATION)
        self.assertEqual(sum(r['event']=='agent_playback_deadline_credit' for r in c.events.rows),1)
        await c.close()
    async def test_session_cancellation_settles_one_lookahead_and_retains_only_played_prefix(self):
        speech=NumericSpeech(100);speech.hold_at=2;played=[]
        async def play(pcm,g):played.append(g)
        c=self.control(speech,play);c.spawn(c.speak(EXPLANATION,1,'normal'));task=c.task
        await asyncio.wait_for(speech.entered.wait(),2);c.interrupt();await asyncio.gather(task,return_exceptions=True)
        self.assertEqual(played,[1]);self.assertEqual(len(speech.calls),2);self.assertTrue(speech.cancelled)
        self.assertEqual(next(r for r in c.history if r.get('partial_output'))['content'][0]['text'],speech_parts(EXPLANATION)[0])
        self.assertEqual(speech.active,0);await c.close()
    async def test_outer_session_timeout_cancels_playback_and_prefetch_without_completion_claim(self):
        speech=NumericSpeech(100);speech.hold_at=2;playing=asyncio.Event()
        async def play(pcm,g):playing.set();await asyncio.Event().wait()
        c=self.control(speech,play)
        with self.assertRaises(TimeoutError):
            async with asyncio.timeout(.15):await c.speak(EXPLANATION,1,'normal')
        self.assertTrue(playing.is_set());self.assertEqual(speech.active,0)
        self.assertFalse(any(r['event']=='response_complete' for r in c.events.rows));await c.close()
    async def test_each_pcm_keeps_45_second_limit_and_capacity_failure_dispatches_nothing(self):
        for seconds,capacity in [(46,100),(.02,1)]:
            speech=Speech(capacity);speech.seconds=seconds;played=[]
            async def play(pcm,g):played.append(g)
            c=self.control(speech,play);self.assertFalse(await c.speak(EXPLANATION,1,'normal'))
            self.assertEqual(played,[]);self.assertEqual(len(speech.calls),1 if seconds==46 else 0);await c.close()
    async def test_short_greeting_and_errors_use_projection_once_without_recursive_delegation(self):
        speech=Speech(10)
        async def play(pcm,g):pass
        c=self.control(speech,play)
        for flags in [{},{'greeting':True},{'error_notice':True}]:
            self.assertTrue(await c.speak('**AI** uses an API.',1,'normal',**flags))
        self.assertEqual([r[1] for r in speech.calls],['artificial intelligence uses an ay, pee, eye.']*3);await c.close()
    async def test_shared_followup_speaks_entire_long_answer_and_retains_original_display_and_context(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='onit-followup-speech-') as tmp:
            events=Events();events.path=Path(tmp)/'events.jsonl';engine=FakeEngine();context=Context();speech=Speech(200);answers=iter(['AI means artificial intelligence.','**Details**\n'+LONG]);seen=[];displays=[]
            async def answer(request,work,evidence,**kw):seen.append((request,kw['history']));return dict(answer=next(answers),goal_id=kw['goal_id'])
            engine.answer=answer;c=Conversation(ROOT,None,SimpleNamespace(),None,events,context,engine=engine,voice=True)
            async def play(pcm,g):pass
            control=controller_class()(speech,c,events,play);control.shared_context=context;c.controller=control
            control.display_reply=lambda g,text,state:displays.append((text,state))
            await control.typed_turn('What is AI?',control.interrupt());cut=len(speech.calls)
            await control.typed_turn('Explain that in more detail.',control.interrupt())
            self.assertEqual(seen[1][1],[dict(task='What is AI?',response='AI means artificial intelligence.')])
            raw='**Details**\n'+LONG;self.assertGreater(len(raw),4141)
            self.assertEqual(''.join(r[1] for r in speech.calls[cut:]),spoken_text(raw))
            self.assertEqual(context.history[-1]['content'][0]['text'],raw);self.assertEqual(displays[-1],(raw,'complete'))
            self.assertEqual(len(seen),2);await control.close()

@unittest.skipUnless(sys.platform=='linux','Actual pinned networkless Onit requires Pi/Linux runtime.')
class NativeFollowupChecks(unittest.IsolatedAsyncioTestCase):
    async def test_original_Onit_accepts_followup_over_4000_chars_without_retry_or_losing_prior_context(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='onit-native-long-followup-') as tmp:
            folder=Path(tmp);captured=[]
            class T(FixtureTransport):
                async def request(self,*a,**kw):captured.append(kw['json']);return await super().request(*a,**kw)
            first='AI means artificial intelligence.';transport=T(response(first),response(LONG));budget=Budget();events=Events();engine=Engine(ROOT,FixtureRegistry(),transport,budget,events)
            one=await engine.answer('What is AI?',folder/'w1',folder/'e1',lazy_tools=True)
            two=await engine.answer('Explain that in detail.',folder/'w2',folder/'e2',history=[dict(task='What is AI?',response=one['answer'])],lazy_tools=True)
            self.assertGreater(len(LONG),4141);self.assertEqual(two['answer'],LONG)
            self.assertEqual((budget.used,transport.calls),(2,2));self.assertTrue(any(m.get('content')==first for m in captured[1]['messages']))
            self.assertTrue(all(r['max_completion_tokens']==2048 for r in captured));self.assertIsNone(engine.task)

if __name__=='__main__':unittest.main(verbosity=2)
