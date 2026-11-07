"""Changed-path contracts with fake providers; actual MCP/Onit where available."""
import asyncio,copy,json,sys,tempfile,unittest,uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
from task_mcp.client import ROOT
from onit_candidate.conversation import Context,Conversation,onit_history,controller_class,submit_text
from onit_candidate.engine import Engine
from onit_candidate.checks import FixtureRegistry,FixtureTransport,response,Events,Budget
from onit_candidate.protocol import CandidateError
from me3_assistant.gui import row
from tests.test_assistant_speech import Speech,TEXT
EXPLANATION=('Supervised learning uses examples that pair an input with a known target. A model learns a relationship from those labeled examples, then predicts a target for a new input. '
             'For example, an email classifier can learn from messages labeled as spam or ordinary mail. During training, it compares each prediction with the label and adjusts its parameters to reduce the error. '
             'You should evaluate the trained model on data it has not seen before. Keeping a separate test set helps reveal whether it learned a useful pattern or merely memorized the training examples. '
             'Classification predicts a category, while regression predicts a numeric value. Both depend on suitable labels, representative data and an evaluation measure that matches the practical goal. '
             'A model that performs well on familiar examples can still fail when the real input differs from its training data, so checking generalization matters.')

class FakeEngine:
    def __init__(self):
        self.task=None;self.gateway=None;self.budget=SimpleNamespace(remaining=lambda r:100);self.calls=[];self.entered=asyncio.Event();self.release=asyncio.Event();self.block=False
    def invalidate(self):
        if self.task:self.task.cancel()
    async def answer(self,request,work,evidence,**kwargs):
        self.calls.append((request,kwargs));self.task=asyncio.current_task();self.entered.set()
        try:
            if self.block:await self.release.wait()
            return dict(answer=TEXT,goal_id=kwargs['goal_id'])
        finally:self.task=None

class MemoryChecks(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='onit-voice-contract-');self.root=Path(self.tmp.name)
        self.events=Events();self.events.path=self.root/'events.jsonl';self.context=Context();self.engine=FakeEngine();self.transport=SimpleNamespace()
        self.c=Conversation(self.root,SimpleNamespace(),self.transport,None,self.events,self.context,engine=self.engine)
        self.g=1;self.c.controller=SimpleNamespace(clock=SimpleNamespace(current=self.g),current=lambda g:g==self.g)
    async def asyncTearDown(self):self.tmp.cleanup()
    async def test_prior_turns_are_native_pairs_and_current_request_occurs_once(self):
        h=[row('user','I am planning a trip.'),row('assistant','Where would you like to go?'),row('user','Seoul, with my family.')]
        await self.c.answer(h);request,kw=self.engine.calls[-1]
        self.assertEqual(request,'Seoul, with my family.')
        self.assertEqual(kw['history'],[dict(task='I am planning a trip.',response='Where would you like to go?')])
        self.assertEqual(kw['context_state']['conversation_id'],self.context.conversation_id)
    async def test_interrupted_goal_retains_identity_and_completed_followup_is_linked(self):
        self.engine.block=True;work=asyncio.create_task(self.c.answer([row('user','First unfamiliar task')]))
        await self.engine.entered.wait();goal=self.context.goal_id;self.c.interrupt()
        with self.assertRaises(asyncio.CancelledError):await work
        self.g=2;self.c.controller.clock.current=2;self.engine.block=False
        await self.c.answer([row('user','Replace it with this correction.')])
        self.assertEqual(self.context.goal_id,goal)
        await self.c.answer([row('user','A later deliberate task')])
        self.assertNotEqual(self.context.goal_id,goal);self.assertEqual(self.context.parent_goal_id,goal)
    async def test_late_model_result_cannot_reach_replaced_controller(self):
        self.engine.block=True;work=asyncio.create_task(self.c.answer([row('user','Old request')]))
        await self.engine.entered.wait();self.g=2;self.engine.release.set()
        with self.assertRaises(asyncio.CancelledError):await work
        self.assertEqual(self.context.state,'interrupted')
    async def test_control_and_partial_speech_are_retained_without_claiming_full_delivery(self):
        h=[row('user','Tell me something'),{**row('assistant','Only a prefix.'),'partial_output':True},
           {**row('assistant','Do not resume the obsolete task.'),'control_state':'cancelled'}]
        pairs=onit_history(h)
        self.assertEqual(pairs,[dict(task='Tell me something',response='Only a prefix.')])
        from onit_candidate.conversation import delivery_metadata
        meta=delivery_metadata(h);self.assertEqual(meta[0]['state'],'cancelled');self.assertFalse(meta[0]['complete_reply_recorded'])
        self.assertNotIn('Do not resume',str(pairs));self.assertNotIn('Host delivery state',str(pairs))
    async def test_typed_input_in_voice_uses_same_history_and_spoken_complete_answer_without_ASR(self):
        speech=Speech(capacity=20);played=[]
        async def play(pcm,g):played.append(g)
        control=controller_class()(speech,self.c,self.events,play);control.shared_context=self.context
        self.c.controller=control;self.c.voice=True;g=control.interrupt()
        await control.typed_turn('Please explain in my existing context.',g)
        self.assertEqual(self.context.history[-1]['content'][0]['text'],TEXT)
        self.assertEqual(''.join(call[1] for call in speech.calls),TEXT);self.assertEqual(len(played),2)
        self.assertIs(control.history,self.context.history);self.assertEqual(len(self.engine.calls),1)
        await control.close()
    async def test_complete_explanation_over_old_character_cutoff_is_all_submitted_to_speech(self):
        explanation=EXPLANATION;speech=Speech(capacity=30);played=[]
        async def answer(*args,**kwargs):return dict(answer=explanation,goal_id=kwargs['goal_id'])
        self.engine.answer=answer
        async def play(pcm,g):played.append(g)
        control=controller_class()(speech,self.c,self.events,play);control.shared_context=self.context;self.c.controller=control;self.c.voice=True
        await control.typed_turn('Please explain this topic.',control.interrupt())
        self.assertGreater(len(explanation),650);self.assertEqual(self.context.history[-1]['content'][0]['text'],explanation)
        self.assertEqual(''.join(call[1] for call in speech.calls),explanation);self.assertGreater(len(played),2)
        await control.close()
    async def test_goodbye_prepares_host_ack_then_stops_after_software_reply_without_model(self):
        stop=[];self.c.voice=True;self.c.request_voice_session_stop=lambda g:stop.append(g)
        reply=await self.c.answer([row('user','Goodbye')])
        self.assertEqual(reply,'Goodbye! Take care.');self.assertEqual(stop,[])
        self.assertEqual(self.c.end_after_reply_generation,1)
        self.c.complete_reply(1)
        self.assertEqual(stop,[1]);self.assertEqual(self.engine.calls,[])
    async def test_capacity_wait_is_credited_to_voice_owner_with_a_cumulative_bound(self):
        waits=[];self.c.voice=True;self.c.active_generation=1
        self.c.controller.credit_provider_wait=lambda g,s:waits.append((g,s))
        self.c.credit_wait(10);self.c.credit_wait(45);self.assertEqual(waits,[(1,10),(1,45)])
        with self.assertRaises(Exception):self.c.credit_wait(6)
        self.g=2
        with self.assertRaises(asyncio.CancelledError):self.c.credit_wait(1)
    async def test_gui_typing_does_not_start_another_voice_or_text_owner(self):
        from onit_candidate.gui import Console
        with patch('onit_candidate.gui.verify_candidate',return_value={}):console=Console({},dict(revision='fixture'))
        console.mode='voice';console.voice_armed=True;console.processor=object();console.stop_event=asyncio.Event();console.task=asyncio.create_task(asyncio.Event().wait())
        try:
            with patch('onit_candidate.gui.submit_text',new_callable=AsyncMock) as submit:
                await console.command(dict(action='send',text='A natural typed correction.'))
                submit.assert_awaited_once_with(console.processor,'A natural typed correction.')
            self.assertIs(console.history,console.shared.history)
        finally:console.task.cancel();await asyncio.gather(console.task,return_exceptions=True)
    async def test_candidate_startup_does_not_open_an_extra_planner_or_audio(self):
        import ast
        package=Path(__file__).parent
        for name in ['voice.py','gui.py','conversation.py','engine.py','gateway.py']:
            tree=ast.parse((package/name).read_text())
            imports=[n.module for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
            self.assertNotIn('me3_assistant.router',imports);self.assertNotIn('me3_assistant.workflow',imports);self.assertNotIn('me3_agent.integration',imports)
        app=(package/'app.js').read_text()
        self.assertIn("current?.active&&current.mode==='voice'",app)

class ConfirmationBridgeChecks(unittest.IsolatedAsyncioTestCase):
    async def test_cross_input_confirmation_keeps_goal_and_requires_delivered_prompt(self):
        from tests import test_assistant_mode as f
        from me3_assistant.registry import Registry
        from me3_assistant.actions import Journal
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='onit-confirm-voice-') as tmp:
            folder=Path(tmp);path=folder/'config.json';path.write_text(json.dumps(f.config('shopping',folder/'test.sqlite3')))
            cfg=f.load(path);events=f.Events(folder/'events.jsonl')
            async with Registry(events,cfg) as registry:
                registry.budget=f.FixtureBudget(1);registry.journal=Journal(folder/'journal.jsonl');registry.allowed_actions=['propose_shopping_item'];registry.session_grant={'allowed_actions':registry.allowed_actions,'workspace_access':False}
                transport=SimpleNamespace();context=Context();goal=context.begin()
                engine=Engine(ROOT,registry,transport,registry.budget,events,enable_actions=True,ledger_path=folder/'intents.jsonl')
                c=Conversation(ROOT,registry,transport,registry.budget,events,context,engine=engine)
                c.controller=SimpleNamespace(clock=SimpleNamespace(current=1),current=lambda g:g in {0,1});c.active_generation=1
                engine.gateway.activate(goal,0)
                proposal=await engine.gateway.execute('propose_shopping_item',{'item':'notebooks','quantity':2},0);prompt=proposal['reply'];context.state='waiting_confirmation'
                self.assertEqual(await c.answer(f.history('Confirm?',prompt)),prompt);self.assertEqual(registry.budget.used,0)
                self.assertEqual(await c.answer(f.history('confirm',prompt,'cancelled')),prompt);self.assertEqual(registry.budget.used,0)
                final=await c.answer(f.history('confirm',prompt))
                self.assertIn('independently checked',final);self.assertEqual(registry.budget.used,1);self.assertEqual(context.goal_id,goal)
                self.assertEqual(context.state,'completed');self.assertFalse(c.pending)

@unittest.skipUnless(sys.platform=='linux','Actual isolated full Onit/Pi native dependencies required.')
class NativeContextChecks(unittest.IsolatedAsyncioTestCase):
    async def test_original_Onit_delivers_complete_explanation_over_old_cutoff_without_extra_model_call(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='onit-final-capacity-') as tmp:
            folder=Path(tmp);explanation=EXPLANATION;t=FixtureTransport(response(explanation));b=Budget();events=Events();e=Engine(ROOT,FixtureRegistry(),t,b,events)
            result=await e.answer('Explain an unfamiliar subject.',folder/'work',folder/'evidence',lazy_tools=True)
            self.assertEqual(result['answer'],explanation);self.assertEqual((b.used,t.calls),(1,1))
            self.assertTrue(any(r['event']=='onit_candidate_final_shape' and r['reply_chars']==len(explanation) and r['text_retained'] is False for r in events.rows))
    async def test_original_Onit_receives_prior_pairs_and_current_request_exactly_once(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='onit-native-context-') as tmp:
            folder=Path(tmp);events=Events();budget=Budget();captured=[]
            class T(FixtureTransport):
                async def request(self,*a,**kw):captured.append(kw['json']);return await super().request(*a,**kw)
            t=T(response('Yes, that preserves your correction.'));e=Engine(ROOT,FixtureRegistry(),t,budget,events)
            result=await e.answer('Use the corrected name.',folder/'work',folder/'evidence',history=onit_history([row('user','The name is Ko Woorim.'),row('assistant','I will use that spelling.')]))
            messages=captured[0]['messages']
            self.assertTrue(any(m.get('content')=='The name is Ko Woorim.' for m in messages))
            self.assertEqual(sum(m.get('content')=='Use the corrected name.' for m in messages),1)
            self.assertEqual(result['answer'],'Yes, that preserves your correction.');self.assertEqual((budget.used,t.calls),(1,1))
    async def test_obsolete_voice_generation_fails_before_model_reservation(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='onit-stale-context-') as tmp:
            folder=Path(tmp);t=FixtureTransport(response());b=Budget();e=Engine(ROOT,FixtureRegistry(),t,b,Events());e.owner_current=lambda:False
            with patch('onit_candidate.engine.asyncio.create_subprocess_exec') as spawn:
                with self.assertRaises(asyncio.CancelledError):await e.answer('Obsolete.',folder/'work',folder/'evidence')
            spawn.assert_not_called()
            self.assertEqual((b.used,t.calls),(0,0));self.assertIsNone(e.task)

class SharedPipelineChecks(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from tests.test_completion_pipeline import PipelineTests
        self.fixture=PipelineTests('test_short_stop_cancels_old_response_and_reuses_probe_for_command')
        with patch('me3_completion.pipeline.ConversationController',controller_class()):await self.fixture.asyncSetUp()
        self.p=self.fixture.p;self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='onit-pipeline-');self.root=Path(self.tmp.name)
        self.context=Context();self.engine=FakeEngine();self.fixture.events.path=self.root/'events.jsonl'
        self.c=Conversation(ROOT,SimpleNamespace(),SimpleNamespace(),None,self.fixture.events,self.context,engine=self.engine,voice=True)
        self.p.controller.provider=self.c;self.c.controller=self.p.controller;self.p.controller.shared_context=self.context
        self.fixture.clients.require_tts_capacity=lambda n:None
        from me3_assistant.overlap import attach
        attach(self.p,self.fixture.clients,self.c,self.fixture.events)
    async def asyncTearDown(self):await self.fixture.asyncTearDown();self.tmp.cleanup()
    async def test_greeting_gates_real_pipeline_capture_until_complete(self):
        from onit_candidate.voice_startup import gate_startup
        from pipecat.audio.vad.vad_analyzer import VADState
        gate_startup(self.p,self.fixture.events)
        await self.fixture.feed(VADState.SPEAKING,20)
        self.assertEqual(len(self.p.pcm),0);self.assertEqual(self.fixture.clients.calls,[])
        self.assertTrue(await self.p.controller.speak('Greeting.',0,'normal',greeting=True))
        self.assertTrue(self.p.candidate_listening)
        await self.fixture.feed(VADState.SPEAKING,20)
        self.assertGreater(len(self.p.pcm),0)
    async def test_typed_replacement_cancels_old_work_and_keeps_same_shared_goal(self):
        self.p.candidate_listening=True;self.engine.block=True
        await submit_text(self.p,'My first natural request.');old=self.p.controller.task
        await self.engine.entered.wait();goal=self.context.goal_id;self.engine.block=False
        self.p.controller.slot={'generation':1,'stage':'asr','pcm':b'obsolete prefix','submitted':__import__('time').monotonic()}
        self.p.controller.saved={'pcm':b'obsolete prefix'}
        await submit_text(self.p,'Actually use this replacement.');await self.p.controller.task
        await asyncio.gather(old,return_exceptions=True)
        self.assertTrue(old.cancelled());self.assertEqual(self.context.goal_id,goal)
        self.assertEqual(self.context.history[-1]['content'][0]['text'],TEXT)
        self.assertTrue(any(r.get('control_state')=='cancelled' for r in self.context.history))
        self.assertEqual(len(self.engine.calls),2);self.assertFalse(any(r[0]=='asr' for r in self.fixture.clients.calls))
        self.assertIsNone(self.p.controller.slot);self.assertIsNone(self.p.controller.saved)
    async def test_acknowledgment_resumes_candidate_pcm_without_new_goal_or_planner(self):
        from me3_completion.pipeline import Ownership
        self.p.candidate_listening=True;self.p.controller.clock.advance();goal=self.context.begin();revision=self.context.revision
        started=asyncio.Event();release=asyncio.Event()
        async def play(pcm,g):started.set();await release.wait()
        self.p.controller.play_audio=play;self.p.controller.spawn(self.p.controller.speak(TEXT,1,'normal'));task=self.p.controller.task
        await started.wait();self.p.output_task=task;self.p.output_generation=1;self.p.output_kind='response';self.p.gate.suspend()
        self.fixture.clients.answer='uh-huh'
        await self.p.classify_overlap(self.p.token,b'fixture-pcm',.5,1,task)
        self.assertTrue(self.p.gate.ready.is_set());self.assertEqual(self.p.ownership,Ownership.RESPONSE)
        self.assertFalse(task.done());self.assertEqual((self.context.goal_id,self.context.revision),(goal,revision));self.assertEqual(self.engine.calls,[])
        release.set();self.assertTrue(await task)

if __name__=='__main__':unittest.main(verbosity=2)
