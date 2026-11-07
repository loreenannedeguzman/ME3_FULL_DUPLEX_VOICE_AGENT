"""Actual staged farewell/SharedController code, inert speech/model/effects."""
import ast,asyncio,importlib.util,os,sys,unittest
from pathlib import Path
from types import ModuleType,SimpleNamespace
from unittest.mock import patch

P=Path(__file__).resolve().parents[1]
ROOT=Path(os.environ.get('ME3_PROJECT_ROOT','/opt/ME3Deployment'));O=Path(os.environ.get('ME3_LATENCY_STAGE',ROOT));sys.path.insert(0,str(ROOT))
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
policy=load('voice_policy252',O/'me3_assistant/voice_policy.py')
conversation=load('conversation252',O/'onit_candidate/conversation.py')

def run(coro):
    try:coro.send(None)
    except StopIteration as done:return done.value
    finally:coro.close()
    raise AssertionError('Unexpected await; step explicitly')
class Boundary:
    def __await__(self):yield 'speech-pending';return None
def history(text):return [dict(type='user_input',content=[dict(type='text',text=text)])]

class Fixture:
    def __init__(self):
        self.current_generation=1;self.stops=[];self.events=[];self.model=[];self.effects=[];self.cancels=[]
        self.context=conversation.Context()
        class Gateway:
            def cancel_pending(inner):
                if inner.actions.pending:self.cancels.append(inner.actions.pending['operation_id'])
                inner.actions.pending=None
        gateway=Gateway();gateway.actions=SimpleNamespace(pending=None);gateway.ledger=SimpleNamespace(load=lambda:{})
        async def answer(*args,**kwargs):self.model.append((args,kwargs));return dict(answer='An ordinary answer.',goal_id=kwargs['goal_id'])
        self.engine=SimpleNamespace(task=None,gateway=gateway,answer=answer,registry=SimpleNamespace(config={'runtime':{'max_session_seconds':60}}),budget=SimpleNamespace(remaining=lambda _:10))
        events=SimpleNamespace(path=P/'.tmp/unused-events.jsonl',emit=lambda name,**fields:self.events.append((name,fields)))
        self.c=conversation.Conversation(P,self.engine.registry,SimpleNamespace(),self.engine.budget,events,self.context,engine=self.engine,voice=True)
        self.c.controller=SimpleNamespace(clock=SimpleNamespace(current=1),current=lambda g:g==self.current_generation)
        self.c.request_voice_session_stop=lambda g:self.stops.append(g) or True
    def answer(self,text):
        with patch.dict(sys.modules,{'me3_assistant.voice_policy':policy}):return run(self.c.answer(history(text)))
    def controller(self,*,played=True,error=None,wait=False):
        fixture=self
        class Speech:
            def current(self,g):return fixture.c.current(g)
            async def speak(self,text,generation,style,**flags):
                fixture.effects.append(('existing-speech',text,generation))
                if wait:await Boundary()
                if error is not None:raise error
                return played
        module=ModuleType('onit_candidate.spoken');module.CandidateSpeech=Speech
        with patch.dict(sys.modules,{'onit_candidate.spoken':module}):cls=conversation.controller_class()
        control=cls();control.provider=self.c;control.shared_context=self.context
        return control

class Classification252(unittest.TestCase):
    def test_existing_grammar_distinguishes_farewell_without_expanding_intents(self):
        for text in ('goodbye','thank you goodbye','Thanks, goodbye for now','Good bye','Bye bye'):
            with self.subTest(text=text):self.assertEqual(policy.voice_session_end_kind(text),'farewell')
        for text in ('stop','please stop listening','end this conversation','Thank you, lets stop this conversation.'):
            with self.subTest(text=text):self.assertEqual(policy.voice_session_end_kind(text),'immediate')
    def test_quoted_negated_targeted_and_background_discussion_are_not_controls(self):
        for text in ('say goodbye','do not say goodbye','when I say goodbye','what does goodbye mean','stop talking','stop music','do not stop listening','okay','thanks','Goodbye and play music'):
            with self.subTest(text=text):self.assertIsNone(policy.voice_session_end_kind(text))
    def test_public_boolean_contract_and_existing_polite_prefix_remain(self):
        for text in ('goodbye','stop','please stop listening'):self.assertIs(policy.ends_voice_session(text),True)
        for text in (None,True,'stop music','x'*257):self.assertIs(policy.ends_voice_session(text),False)
        with patch.dict(sys.modules,{'me3_assistant.voice_policy':policy}):
            self.assertEqual(conversation.candidate_session_end_kind('Okay, thank you goodbye'),'farewell')
            self.assertTrue(conversation.ends_candidate_session('Alright, stop listening'))

class Farewell252(unittest.TestCase):
    def test_direct_farewell_returns_ack_no_model_and_defers_stop(self):
        f=Fixture();reply=f.answer('thank you goodbye')
        self.assertEqual(reply,'Goodbye! Take care.');self.assertEqual(f.model,[]);self.assertEqual(f.stops,[])
        self.assertEqual(f.c.end_after_reply_generation,1)
        self.assertEqual(f.context.state,'canceled') # Earlier unfinished work is not marked complete.
    def test_existing_speech_completes_before_current_owner_stop(self):
        f=Fixture();reply=f.answer('goodbye');control=f.controller(wait=True)
        coro=control.speak(reply,1,'normal');self.assertEqual(coro.send(None),'speech-pending')
        self.assertEqual(f.stops,[])
        with self.assertRaises(StopIteration) as done:coro.send(None)
        self.assertIs(done.exception.value,True);self.assertEqual(f.stops,[1]);self.assertEqual(f.model,[])
        self.assertEqual(f.context.state,'closed');self.assertIsNone(f.c.end_after_reply_generation)
        self.assertTrue(any(name=='onit_session_end_after_reply' and data['acoustic_completion_measured'] is False for name,data in f.events))
    def test_failed_speech_closes_same_owner_without_claiming_completed_reply(self):
        f=Fixture();reply=f.answer('goodbye')
        self.assertFalse(run(f.controller(played=False).speak(reply,1,'normal')))
        self.assertEqual(f.stops,[1]);self.assertEqual(f.context.state,'closed')
        self.assertFalse(any(name=='onit_session_end_after_reply' for name,_ in f.events))
        self.assertTrue(any(name=='onit_session_end_without_complete_reply' for name,_ in f.events))
    def test_synthesis_exception_or_exhaustion_cannot_leave_listening(self):
        from speech_mcp.client import ProviderLimited
        for error in (ValueError('authored synthesis failure'),ProviderLimited('authored exhausted')):
            f=Fixture();reply=f.answer('goodbye')
            with self.subTest(error=type(error).__name__),self.assertRaises(type(error)):
                run(f.controller(error=error).speak(reply,1,'normal'))
            self.assertEqual(f.stops,[1]);self.assertIsNone(f.c.end_after_reply_generation)
    def test_current_cancellation_stops_but_new_turn_cancellation_cannot_stop_new_owner(self):
        for new_turn in (False,True):
            f=Fixture();reply=f.answer('goodbye');coro=f.controller(wait=True).speak(reply,1,'normal');coro.send(None)
            if new_turn:f.c.interrupt();f.current_generation=2
            with self.assertRaises(asyncio.CancelledError):coro.throw(asyncio.CancelledError())
            self.assertEqual(f.stops,[] if new_turn else [1])
    def test_fresh_interrupt_before_play_completion_never_closes_new_turn(self):
        f=Fixture();reply=f.answer('goodbye');coro=f.controller(wait=True).speak(reply,1,'normal');coro.send(None)
        f.c.interrupt();f.current_generation=2
        with self.assertRaises(StopIteration):coro.send(None)
        self.assertEqual(f.stops,[]);self.assertIsNone(f.c.end_after_reply_generation)
    def test_hard_stop_keeps_immediate_zero_speech_and_zero_model_path(self):
        for text in ('stop','please stop listening','end the session'):
            f=Fixture()
            with self.subTest(text=text),self.assertRaises(asyncio.CancelledError):f.answer(text)
            self.assertEqual(f.stops,[1]);self.assertEqual(f.effects,[]);self.assertEqual(f.model,[])
    def test_pending_review_canceled_without_confirmation_commit_or_replay(self):
        f=Fixture();f.engine.gateway.actions.pending={'operation_id':'a'*32,'prompt':'Exact private proposal.'}
        f.answer('thank you goodbye')
        self.assertEqual(f.cancels,['a'*32]);self.assertIsNone(f.c.pending)
        self.assertEqual(f.model,[]);self.assertEqual(f.effects,[])
    def test_ordinary_reply_and_unbound_text_mode_do_not_arm_session_end(self):
        f=Fixture();self.assertEqual(f.answer('Explain this example.'),'An ordinary answer.')
        self.assertEqual(len(f.model),1);self.assertIsNone(f.c.end_after_reply_generation)
        self.assertFalse(run(f.controller(played=False).speak('An ordinary answer.',1,'normal')));self.assertEqual(f.stops,[])
        f=Fixture();f.c.voice=False;f.answer('goodbye');self.assertEqual(len(f.model),1);self.assertEqual(f.stops,[])
    def test_gui_stop_source_is_not_in_overlay_and_session_tool_is_unchanged(self):
        # Deployment source/manifest checks pin untouched GUI/session-end bytes.
        # In a staged overlay, their absence also makes that scope explicit.
        if (P/'overlay').is_dir():
            self.assertFalse((O/'onit_candidate/gui.py').exists())
            self.assertFalse((O/'onit_candidate/session_end.py').exists())
        for name in ('onit_candidate/conversation.py','me3_assistant/voice_policy.py','onit_candidate/voice_checks.py'):ast.parse((O/name).read_text())

if __name__=='__main__':unittest.main(verbosity=2)
