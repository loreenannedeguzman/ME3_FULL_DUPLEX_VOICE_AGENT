"""No devices/providers: interrupted display, stop admission and private diagnostics."""
import asyncio,json,unittest
from types import SimpleNamespace as NS
from unittest.mock import patch
from me3_assistant.gui import Console,row
from me3_assistant.voice_policy import reserve_open_session
from me3_assistant.groq_backend import Transport
from me3_assistant.groq_json import JSONGroqReasoning
from tests.test_assistant_speech import Speech,Events,TEXT
from me3_assistant.speech import AssistantController
from tests.test_assistant_mode import config
from tests.test_assistant_groq_adapter import Session,Reply,fake_transport,answer
from tests.test_assistant_capacity_gui import seeded

class DisplayTests(unittest.IsolatedAsyncioTestCase):
    async def test_interruption_before_first_chunk_finishes_is_visible_without_history_commit(self):
        for text in ('Which book would you like to publish?',TEXT):
            with self.subTest(text=text):
                playing=asyncio.Event()
                async def play(pcm,g):playing.set();await asyncio.Event().wait()
                c=AssistantController(Speech(),None,Events(),play);c.clock.advance();c.history=[row('user','Publish my manuscript')];c.pending_history_generation=1
                gui=Console(config(),{'revision':'fixture'});gui.bind_controller(c)
                c.spawn(c.speak(text,1,'normal'));await asyncio.wait_for(playing.wait(),1)
                self.assertEqual(gui.display_conversation(c.history)[-1]['text'],text)
                self.assertFalse(any(r['type']=='model_output' for r in c.history))
                c.interrupt();await asyncio.gather(c.task,return_exceptions=True)
                gui.history=list(c.history);gui.controller=None
                m=gui.display_conversation(gui.history);self.assertEqual(m[-1]['state'],'interrupted');self.assertEqual(m[-1]['text'],text)
                self.assertNotIn(text,str(c.events.rows));self.assertFalse(any(text in str(r) for r in c.history))
                await c.close()
    async def test_complete_and_partial_rows_are_not_duplicated_and_anchors_are_identity_bound(self):
        gui=Console(config(),{'revision':'fixture'});first=row('user','same question');c=NS(history=[first]);gui.bind_controller(c)
        c.display_reply(1,'first reply','preparing');c.history.append({**row('assistant','first'),'partial_output':True})
        self.assertEqual(len(gui.display_conversation(c.history)),2)
        c.history[-1]=row('assistant','first reply');c.display_reply(1,'first reply','complete')
        second=row('user','same question');c.history.append(second);c.display_reply(2,'second reply','interrupted')
        self.assertEqual([m['text'] for m in gui.display_conversation(c.history)],['same question','first reply','same question','second reply'])
        self.assertEqual([m['text'] for m in gui.display_conversation(c.history[2:])],['same question','second reply'])
        gui.history=c.history;gui.controller=None;await gui.command({'action':'clear'});self.assertEqual(gui.voice_replies,[])
    async def test_greeting_and_error_notice_are_not_inserted_as_answers(self):
        async def play(pcm,g):pass
        c=AssistantController(Speech(4),None,Events(),play);c.clock.advance();c.history=[row('user','question')]
        gui=Console(config(),{'revision':'fixture'});gui.bind_controller(c)
        await c.speak('greeting',1,'normal',greeting=True);await c.speak('error',1,'normal',error_notice=True)
        self.assertEqual(gui.voice_replies,[]);await c.close()

class StopAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_failure_blocks_every_new_reservation_including_error_notice(self):
        gui=Console(config(),{'revision':'fixture'});gui.mode='voice';gui.stop_event=asyncio.Event();gui.task=asyncio.create_task(asyncio.Event().wait())
        used=[];budget=NS(reserve=lambda role:used.append(role),remaining=lambda role:10)
        gui.observe('assistant_reasoning_recoverable_failure')
        for role in ('asr','reasoning','tts'):
            with self.assertRaises(asyncio.CancelledError):reserve_open_session(budget,role,gui.stop_event)
        self.assertEqual(used,[]);gui.task.cancel();await asyncio.gather(gui.task,return_exceptions=True)
    async def test_stop_during_capacity_wait_prevents_lease_and_wire_without_refund(self):
        now=[1000.];admission=seeded(now);before=json.dumps(admission.memory,sort_keys=True);s=Session(Reply());t=Transport(lambda *a,**k:None,key='fixture',session=s,admission=admission);t.stop_requested=asyncio.Event()
        async def sleep(delay):now[0]+=delay;t.stop_requested.set()
        with patch('me3_assistant.groq_backend.asyncio.sleep',sleep),self.assertRaises(asyncio.CancelledError):await t.request('reasoning',capacity_wait_seconds=20,json={})
        self.assertEqual(s.calls,[]);self.assertFalse(t.active);self.assertEqual(json.dumps(admission.memory,sort_keys=True),before)
    async def test_already_stopped_transport_never_enters_admission(self):
        s=Session(Reply());t=Transport(lambda *a,**k:None,key='fixture',session=s);t.stop_requested=asyncio.Event();t.stop_requested.set()
        with patch.object(t.admission,'admit') as admit,self.assertRaises(asyncio.CancelledError):await t.request('asr')
        admit.assert_not_called();self.assertEqual(s.calls,[])

class RejectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejections_are_classified_without_private_values_or_automatic_repair(self):
        declarations=[dict(name='calculate',parameters={'type':'object','properties':{},'additionalProperties':False})]
        for raw,code in [('private malformed text','json_syntax'),(json.dumps({'speech':'private','calls':[{'name':'__no_tools_available__','arguments_json':'{}'}]}),'schema_validation'),(json.dumps({'speech':19,'calls':[]}),'schema_validation'),(json.dumps({'speech':'','calls':[]}),'empty_assistant_decision')]:
            transport=fake_transport(answer(raw));events=Events();m=JSONGroqReasoning(events,transport=transport)
            with self.subTest(code=code),self.assertRaisesRegex(ValueError,'invalid_json_object_decision'):await m.decide([],declarations,[])
            detail=next(e for e in events.rows if e['event']=='groq_decision_rejection_detail')
            self.assertEqual(detail['reason'],code);self.assertNotIn('private',str(events.rows));transport.request.assert_awaited_once();self.assertFalse(m.lock.locked())
