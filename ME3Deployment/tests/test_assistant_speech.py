import asyncio,base64,re,unittest
from me3_assistant.speech import AssistantController,response_parts
from task_mcp.reasoner import prompt_delivered

TEXT='Warm water rises and cools into cloud droplets. When droplets become heavy enough, they fall to the ground as rain in a continuing water cycle.'
COUNT=', '.join(str(i) for i in range(1,21))+'.'

class Events:
    def __init__(self):self.rows=[]
    def emit(self,event,**kw):self.rows.append({'event':event,**kw})

class Speech:
    def __init__(self,capacity=2):self.capacity=capacity;self.calls=[];self.wait=asyncio.Event();self.entered=asyncio.Event();self.cancelled=False;self.hold=False;self.ignore_cancel=False;self.seconds=.02
    def require_tts_capacity(self,n):
        if self.capacity<n:raise ValueError('insufficient_complete_response_allowance')
    async def call(self,role,g,**args):
        self.calls.append((g,args['text'],args['style']));self.capacity-=1
        if len(self.calls)==2 and self.hold:
            self.entered.set()
            try:await self.wait.wait()
            except asyncio.CancelledError:
                self.cancelled=True
                if not self.ignore_cancel:raise
        return {'pcm_b64':base64.b64encode(b'\x01\x00'*int(24000*self.seconds)).decode()}

class SplitTests(unittest.TestCase):
    def test_no_word_loss_decimal_damage_or_unbounded_chunk_count(self):
        for text in [TEXT,'Read 3.14 and 18.5 percent, followed by all six saved quantities. '*8,'x'*200,'short reply']:
            parts=response_parts(text)
            self.assertEqual(''.join(parts),text);self.assertTrue(all(parts));self.assertLessEqual(len(parts),1+(len(text)+34)//35)
            self.assertTrue(all(len(part)<=160 or not re.search(r'\s',part.strip()) for part in parts))
            if len(parts)==2:self.assertLessEqual(len(parts[0]),120);self.assertFalse(parts[0].endswith('3.'))

class SpeechTests(unittest.IsolatedAsyncioTestCase):
    def controller(self,speech,play):
        c=AssistantController(speech,None,Events(),play);c.clock.advance();c.history=[{'type':'user_input','content':[{'type':'text','text':'explain rain'}]}];c.pending_history_generation=1
        return c
    async def test_prefix_plays_before_remaining_synthesis_finishes_and_full_context_is_not_duplicated(self):
        speech=Speech();speech.hold=True;played=[]
        async def play(pcm,g):played.append(g)
        c=self.controller(speech,play)
        work=asyncio.create_task(c.speak(TEXT,1,'normal'))
        await asyncio.wait_for(speech.entered.wait(),1)
        self.assertEqual(played,[1]);self.assertFalse(work.done());self.assertEqual(len(c.history),2)
        speech.wait.set();self.assertTrue(await work)
        self.assertEqual(played,[1,1]);self.assertEqual(''.join(x[1] for x in speech.calls),TEXT);self.assertEqual(len(c.history),1)
        self.assertEqual(len(speech.calls),2);await c.close()
    async def test_cancellation_keeps_only_submitted_prefix_and_settles_prefetch(self):
        speech=Speech();speech.hold=True;played=[]
        async def play(pcm,g):played.append(g)
        c=self.controller(speech,play);c.spawn(c.speak(TEXT,1,'normal'));work=c.task
        await asyncio.wait_for(speech.entered.wait(),1);c.interrupt()
        await asyncio.gather(work,return_exceptions=True)
        self.assertTrue(speech.cancelled);self.assertEqual(played,[1])
        prefix=next(x for x in c.history if x.get('partial_output'))
        self.assertEqual(prefix['content'][0]['text'],response_parts(TEXT)[0]);self.assertNotIn(TEXT,str(prefix))
        self.assertFalse(prompt_delivered(c.history+[{'type':'user_input','content':[{'type':'text','text':'confirm'}]}],TEXT))
        await c.close()
    async def test_late_prefetch_reply_after_cancel_cannot_replay_obsolete_audio(self):
        speech=Speech();speech.hold=True;speech.ignore_cancel=True;played=[]
        async def play(pcm,g):played.append(g)
        c=self.controller(speech,play);c.spawn(c.speak(TEXT,1,'normal'));old=c.task
        await asyncio.wait_for(speech.entered.wait(),1);c.interrupt();await asyncio.gather(old,return_exceptions=True)
        self.assertEqual(played,[1]);self.assertTrue(speech.cancelled);await c.close()
    async def test_insufficient_complete_answer_capacity_dispatches_nothing(self):
        speech=Speech(1);played=[]
        async def play(pcm,g):played.append(g)
        c=self.controller(speech,play);self.assertFalse(await c.speak(TEXT,1,'normal'))
        self.assertEqual(speech.calls,[]);self.assertEqual(played,[]);await c.close()
    async def test_short_greeting_and_error_remain_one_call_each(self):
        speech=Speech(8)
        async def play(pcm,g):pass
        c=self.controller(speech,play)
        for text,flags in [('short answer',{}),(TEXT,{'greeting':True}),(TEXT,{'error_notice':True})]:self.assertTrue(await c.speak(text,1,'normal',**flags))
        self.assertEqual(len(speech.calls),3);await c.close()
    async def test_combined_audio_limit_cannot_be_multiplied_by_partitioning(self):
        speech=Speech();speech.seconds=24;played=[]
        async def play(pcm,g):played.append(g)
        c=self.controller(speech,play);self.assertFalse(await c.speak(TEXT,1,'normal'))
        self.assertEqual(played,[1]);self.assertTrue(any(x.get('partial_output') for x in c.history));await c.close()
    async def test_actual_turn_records_full_answer_only_after_both_parts(self):
        speech=Speech();original=speech.call
        async def call(role,g,**args):return {'text':'Explain rain.'} if role=='asr' else await original(role,g,**args)
        speech.call=call
        class Model:
            async def answer(self,history):return TEXT
        async def play(pcm,g):pass
        c=AssistantController(speech,Model(),Events(),play);g=c.interrupt();c.submit(bytes(6400),g);await c.task
        self.assertEqual(c.history[-1]['content'][0]['text'],TEXT);self.assertFalse(any(x.get('partial_output') for x in c.history));await c.close()

class NumericSplitTests(unittest.TestCase):
    def test_five_items_per_part_with_exact_order_punctuation_and_whitespace(self):
        for count in (8,10,11,15,16,20):
            text='  '+', '.join(str(i) for i in range(1,count+1))+'.  '
            parts=response_parts(text)
            self.assertEqual(''.join(parts),text)
            self.assertEqual(len(parts),(count+4)//5)
            for part in parts:self.assertLessEqual(len(re.findall(r'[0-9]+',part)),5)
        self.assertEqual(len(response_parts(COUNT)),4)
        self.assertEqual(response_parts(COUNT)[0],'1, 2, 3, 4, 5, ')

    def test_decimals_units_thousands_and_prose_do_not_gain_numeric_partitioning(self):
        for text in ('1.1, 2.2, 3.3, 4.4, 5.5, 6.6, 7.7, 8.8.',
                     '1,000, 2,000, 3,000, 4,000, 5,000, 6,000, 7,000, 8,000.',
                     '1 kg, 2 kg, 3 kg, 4 kg, 5 kg, 6 kg, 7 kg, 8 kg.',
                     'Count: '+COUNT, ', '.join(str(i) for i in range(1,8))+'.'):
            parts=response_parts(text)
            self.assertEqual(''.join(parts),text)
            self.assertTrue(all(len(part)<=160 for part in parts)) # Prose is length-bounded; five-number grouping is reserved for the bare numeric grammar.
        larger=', '.join(str(i) for i in range(1,80))+'.'
        self.assertEqual(''.join(response_parts(larger)),larger)
        self.assertTrue(all(len(part)<=160 for part in response_parts(larger)))

class NumericSpeech(Speech):
    def __init__(self,capacity=4):
        super().__init__(capacity);self.hold_at=0;self.fail_at=0;self.active=0;self.max_active=0
    async def call(self,role,g,**args):
        self.calls.append((g,args['text'],args['style']));self.capacity-=1
        number=len(self.calls);self.active+=1;self.max_active=max(self.max_active,self.active)
        try:
            if number==self.hold_at:
                self.entered.set()
                try:await self.wait.wait()
                except asyncio.CancelledError:
                    self.cancelled=True
                    if not self.ignore_cancel:raise
            if number==self.fail_at:raise ValueError('fixed_fixture_synthesis_failure')
            return {'pcm_b64':base64.b64encode(b'\x01\x00'*int(24000*self.seconds)).decode()}
        finally:self.active-=1

class NumericSpeechTests(unittest.IsolatedAsyncioTestCase):
    controller=SpeechTests.controller
    async def test_numeric_parts_preserve_style_order_and_one_complete_context(self):
        speech=NumericSpeech();played=[]
        async def play(pcm,g):played.append(g)
        c=self.controller(speech,play);self.assertTrue(await c.speak(COUNT,1,'slow'))
        self.assertEqual(played,[1]*4);self.assertEqual(''.join(x[1] for x in speech.calls),COUNT)
        self.assertEqual([x[2] for x in speech.calls],['slow']*4)
        self.assertFalse(any(x.get('partial_output') for x in c.history))
        self.assertEqual(speech.max_active,1);await c.close()

    async def test_capacity_for_entire_numeric_response_is_required_before_first_call(self):
        speech=NumericSpeech(3);played=[]
        async def play(pcm,g):played.append(g)
        c=self.controller(speech,play);self.assertFalse(await c.speak(COUNT,1,'slow'))
        self.assertEqual(speech.calls,[]);self.assertEqual(played,[]);await c.close()

    async def test_only_one_lookahead_starts_while_first_part_is_playing(self):
        speech=NumericSpeech();speech.hold_at=2;playing=asyncio.Event();release=asyncio.Event();played=[]
        async def play(pcm,g):
            played.append(g)
            if len(played)==1:playing.set();await release.wait()
        c=self.controller(speech,play);work=asyncio.create_task(c.speak(COUNT,1,'slow'))
        await asyncio.wait_for(playing.wait(),1);await asyncio.wait_for(speech.entered.wait(),1)
        self.assertEqual(len(speech.calls),2);self.assertEqual(played,[1]);self.assertFalse(work.done())
        release.set();speech.wait.set();self.assertTrue(await work);await c.close()

    async def test_third_part_cancel_retains_only_first_ten_numbers(self):
        for late in (False,True):
            speech=NumericSpeech();speech.hold_at=3;speech.ignore_cancel=late;played=[]
            async def play(pcm,g):played.append(g)
            c=self.controller(speech,play);c.spawn(c.speak(COUNT,1,'slow'));old=c.task
            await asyncio.wait_for(speech.entered.wait(),1);c.interrupt();await asyncio.gather(old,return_exceptions=True)
            self.assertEqual(played,[1,1]);self.assertTrue(speech.cancelled)
            self.assertEqual(len(speech.calls),3)
            prefix=next(x for x in c.history if x.get('partial_output'))
            self.assertEqual(prefix['content'][0]['text'],''.join(response_parts(COUNT)[:2]))
            self.assertFalse(prompt_delivered(c.history+[{'type':'user_input','content':[{'type':'text','text':'confirm'}]}],COUNT))
            self.assertEqual(speech.active,0);await c.close()

    async def test_failure_in_third_part_never_plays_or_dispatches_fourth(self):
        speech=NumericSpeech();speech.fail_at=3;played=[]
        async def play(pcm,g):played.append(g)
        c=self.controller(speech,play);self.assertFalse(await c.speak(COUNT,1,'slow'))
        self.assertEqual(played,[1,1]);self.assertEqual(len(speech.calls),3)
        prefix=next(x for x in c.history if x.get('partial_output'))
        self.assertEqual(prefix['content'][0]['text'],''.join(response_parts(COUNT)[:2]));await c.close()

    async def test_four_parts_share_one_audio_limit_and_do_not_multiply_grace(self):
        speech=NumericSpeech();speech.seconds=12;played=[]
        async def play(pcm,g):played.append(g)
        c=self.controller(speech,play);self.assertFalse(await c.speak(COUNT,1,'slow'))
        self.assertEqual(played,[1]*3);self.assertEqual(len(speech.calls),4)
        self.assertTrue(any(x['event']=='turn_error' for x in c.events.rows));await c.close()
        speech=NumericSpeech();played=[];c=self.controller(speech,play);c.synthesis_budget_seconds=0
        async with asyncio.timeout(1) as timer:
            before=timer.when();c.deadlines[1]={'timer':timer,'audio_credited':False}
            self.assertTrue(await c.speak(COUNT,1,'slow'))
            self.assertAlmostEqual(timer.when()-before,10+4*speech.seconds,places=5)
        self.assertEqual(len([x for x in c.events.rows if x['event']=='agent_playback_deadline_credit']),1)
        self.assertEqual(len([x for x in c.events.rows if x['event']=='assistant_remaining_playback_credit']),3);await c.close()

    async def test_numeric_greeting_and_error_do_not_change_the_single_call_path(self):
        speech=NumericSpeech();played=[]
        async def play(pcm,g):played.append(g)
        c=self.controller(speech,play)
        self.assertTrue(await c.speak(COUNT,1,'normal',greeting=True))
        self.assertTrue(await c.speak(COUNT,1,'normal',error_notice=True))
        self.assertEqual(len(speech.calls),2);await c.close()

    async def test_actual_numeric_turn_records_whole_answer_only_after_four_parts(self):
        speech=NumericSpeech();original=speech.call
        async def call(role,g,**args):return {'text':'Count slowly from one to twenty.'} if role=='asr' else await original(role,g,**args)
        speech.call=call
        class Model:
            async def answer(self,history):return COUNT
        async def play(pcm,g):pass
        c=AssistantController(speech,Model(),Events(),play);g=c.interrupt();c.submit(bytes(6400),g);await c.task
        self.assertEqual(c.history[-1]['content'][0]['text'],COUNT)
        self.assertFalse(any(x.get('partial_output') for x in c.history));await c.close()
