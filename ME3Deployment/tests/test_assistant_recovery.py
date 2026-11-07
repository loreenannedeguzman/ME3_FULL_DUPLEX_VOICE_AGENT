"""Normal-mode failure recovery with real fixture MCP, never hosted services."""
import asyncio,base64
from types import SimpleNamespace
from tests import test_assistant_mode as fixtures
from me3_assistant.actions import Journal
from me3_assistant.router import Router
import unittest

class ServiceError(Exception):
    def __init__(self,code):super().__init__('fixture_failure');self.code=code

class FaultModel(fixtures.Model):
    async def decide(self,h,d,c):
        self.seen.append((h,d,list(c)));value=next(self.answers)
        if isinstance(value,BaseException):raise value
        return value

class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixtures.MCPTests.asyncSetUp
    asyncTearDown=fixtures.MCPTests.asyncTearDown
    cfg=fixtures.MCPTests.cfg
    def router(self,registry,*answers):
        registry.budget=fixtures.FixtureBudget(0);registry.journal=Journal(self.folder/'journal.jsonl');registry.allowed_actions=[]
        model=FaultModel(*answers);return Router(model,registry,self.events),model
    async def test_504_then_new_tool_query_recovers_on_same_MCP_connection_without_retry(self):
        async with fixtures.Registry(self.events,self.cfg('extension')) as r:
            router,m=self.router(r,ServiceError(504),fixtures.decision('configured__count_words',{'text':'one two'}),fixtures.decision(text='Two words.'))
            first=await router.answer(fixtures.history('Explain a concept.'))
            self.assertIn('could not finish',first);self.assertEqual(len(m.seen),1);self.assertEqual(r.calls,0)
            self.assertEqual(await router.answer(fixtures.history('Count one two')), 'Two words.')
            self.assertEqual(len(m.seen),3);self.assertEqual(r.calls,1)
    async def test_failure_after_completed_read_does_not_repeat_it_or_claim_rollback(self):
        async with fixtures.Registry(self.events,self.cfg('extension')) as r:
            router,m=self.router(r,fixtures.decision('configured__count_words',{'text':'one two'}),ServiceError(503),fixtures.decision(text='A new answer.'))
            first=await router.answer(fixtures.history('Count one two'))
            self.assertIn('could not finish',first);self.assertEqual(r.calls,1)
            self.assertNotIn('undone',first);self.assertEqual(await router.answer(fixtures.history('New topic')), 'A new answer.')
            self.assertEqual(r.calls,1);self.assertEqual(len(m.seen),3)
    async def test_quota_and_authentication_errors_still_propagate(self):
        async with fixtures.Registry(self.events,self.cfg('extension')) as r:
            for code in (429,401,403):
                router,m=self.router(r,ServiceError(code))
                with self.subTest(code=code),self.assertRaises(ServiceError):await router.answer(fixtures.history('A question'))
                self.assertEqual(len(m.seen),1)
    async def test_cancellation_is_never_converted_to_a_spoken_recovery_notice(self):
        async with fixtures.Registry(self.events,self.cfg('extension')) as r:
            router,m=self.router(r,asyncio.CancelledError())
            with self.assertRaises(asyncio.CancelledError):await router.answer(fixtures.history('An obsolete request'))
            self.assertFalse(any(e['event']=='assistant_reasoning_recoverable_failure' for e in self.events.rows))
    async def test_late_failure_cannot_speak_for_a_superseded_request(self):
        async with fixtures.Registry(self.events,self.cfg('extension')) as r:
            router,m=self.router(r);active=[True]
            router.controller=SimpleNamespace(clock=SimpleNamespace(current=1),current=lambda g:active[0])
            async def late(*args):active[0]=False;raise ServiceError(504)
            m.decide=late
            self.assertEqual(await router.answer(fixtures.history('Obsolete question')),'')
            self.assertFalse(any(e['event']=='assistant_reasoning_recoverable_failure' for e in self.events.rows))
    async def test_unexpected_model_fault_still_fails_closed(self):
        async with fixtures.Registry(self.events,self.cfg('extension')) as r:
            router,m=self.router(r,ValueError('unexpected_protocol_fault'))
            with self.assertRaisesRegex(ValueError,'unexpected_protocol_fault'):await router.answer(fixtures.history('A question'))
    async def test_timeout_and_empty_decision_each_wait_for_next_user_without_retry(self):
        async with fixtures.Registry(self.events,self.cfg('extension')) as r:
            for error in (TimeoutError(),ValueError('empty_assistant_decision')):
                router,m=self.router(r,error,fixtures.decision(text='Recovered.'))
                self.assertIn('could not finish',await router.answer(fixtures.history('A question')))
                self.assertEqual(len(m.seen),1)
                self.assertEqual(await router.answer(fixtures.history('A new question')),'Recovered.')
                self.assertEqual(len(m.seen),2)
    async def test_actual_normal_controller_keeps_session_usable_after_clean_model_failure(self):
        from me3_assistant.speech import AssistantController
        async with fixtures.Registry(self.events,self.cfg('extension')) as r:
            router,m=self.router(r,ServiceError(504),fixtures.decision(text='Tokyo.'))
            failures=[];played=[]
            class Speech:
                def require_tts_capacity(self,count):pass # Complete-response fixture capacity; no provider/budget.
                async def call(self,role,g,**kw):
                    if role=='asr':return {'text':'A complete question.','generation':g,'request_id':'fixture-asr'}
                    return {'pcm_b64':base64.b64encode(b'\x01\x00'*480).decode()}
                def mark_failure(self,*args):failures.append(args)
            async def play(pcm,g):played.append(g)
            c=AssistantController(Speech(),router,self.events,play);router.controller=c
            try:
                for _ in range(2):
                    g=c.interrupt();c.submit(bytes(6400),g);await c.task
                self.assertEqual(failures,[]);self.assertEqual(set(played),{1,2}) # Both complete responses may have multiple PCM parts.
                self.assertEqual(c.history[-1]['content'][0]['text'],'Tokyo.')
                self.assertEqual(len(m.seen),2)
            finally:await c.close()
    def test_GUI_reports_failed_turn_without_exposing_service_error_details(self):
        from me3_assistant.gui import Console
        cfg=fixtures.config();cfg['sha256']='fixture'
        console=Console(cfg,{'revision':'fixture'})
        console.observe('assistant_reasoning_recoverable_failure',generation=1,error_type='fixture_private_detail')
        self.assertIn('new request',console.notice)
        self.assertNotIn('fixture_private_detail',str(console.events))
