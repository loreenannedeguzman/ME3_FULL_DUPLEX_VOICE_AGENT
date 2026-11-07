"""Bounded private-error classification and explicit reasoning effort; no APIs."""
import json,unittest
from unittest.mock import AsyncMock
from types import SimpleNamespace
from me3_assistant import groq_backend as g
from tests import test_assistant_groq_adapter as a
from tests import test_assistant_mode as f
from tests import test_assistant_recovery as recovery
from me3_assistant.actions import Journal
from me3_assistant.router import Router

class PlanningMetadataTests(unittest.IsolatedAsyncioTestCase):
    async def test_fragmented400_only_classifies_safe_fields_without_retaining_body_or_args(self):
        body=json.dumps({'error':{'code':'tool_use_failed','message':'PRIVATE_ERROR_AND_KEY','failed_generation':'<function=run_goal_steps>{"steps":[{"PRIVATE_FIELD":"PRIVATE_VALUE"}]}</function>'}}).encode()
        tool={'type':'function','function':{'name':'run_goal_steps','parameters':{'type':'object','properties':{'steps':{'type':'array','maxItems':0}},'required':['steps'],'additionalProperties':False}}}
        reply=a.Reply(400,parts=[body[:17],body[17:],b'']);events=a.Events();session=a.Session(reply);transport=g.Transport(events.emit,key='PRIVATE_KEY',session=session)
        with self.assertRaises(g.ProviderError) as caught:await transport.request('reasoning',json={'tools':[tool]})
        self.assertEqual(caught.exception.provider_error_code,'tool_use_failed');self.assertEqual(len(session.calls),1)
        event=next(fields for name,fields in events.rows if name=='groq_planning_rejection')
        self.assertTrue(event['failed_call_registered']);self.assertEqual(event['failed_plan_steps'],1);self.assertEqual(event['failed_schema_validator'],'maxItems')
        self.assertFalse(event['body_retained']);self.assertNotIn('PRIVATE',json.dumps(events.rows));self.assertFalse(transport.active)
    async def test_malformed_oversize_or_unknown_error_does_not_leak_or_retry(self):
        for parts in ([b'PRIVATE_NON_JSON',b''],[b'x'*131073],[b'{"error":{"code":"PRIVATE_CODE"}}',b'']):
            events=a.Events();session=a.Session(a.Reply(400,parts=parts));t=g.Transport(events.emit,key='PRIVATE',session=session)
            with self.assertRaises(g.ProviderError) as caught:await t.request('reasoning',json={})
            self.assertEqual(caught.exception.provider_error_code,'unclassified');self.assertEqual(len(session.calls),1);self.assertNotIn('PRIVATE',json.dumps(events.rows));self.assertFalse(t.active)
    async def test_only_reasoning400_reads_error_body_other_failures_keep_original_policy(self):
        for role,status in (('reasoning',429),('asr',400),('reasoning',401)):
            reply=a.Reply(status);session=a.Session(reply);t=g.Transport(lambda *a,**k:None,key='fixture',session=session)
            with self.assertRaises(g.ProviderError):await t.request(role)
            reply.content.read.assert_not_awaited();self.assertEqual(len(session.calls),1)
    async def test_effort_is_explicit_bounded_and_logged_without_changing_model_or_tool_execution(self):
        for effort in ('low','medium'):
            transport=a.fake_transport(a.answer());events=a.Events();model=g.GroqReasoning(events,transport=transport,reasoning_effort=effort)
            await model.decide([],[],[]);payload=transport.request.call_args.kwargs['json']
            self.assertEqual(payload['reasoning_effort'],effort);self.assertEqual(payload['model'],g.TEXT_MODEL);self.assertEqual(payload['max_completion_tokens'],1024)
            self.assertTrue(any(fields.get('reasoning_effort')==effort for _,fields in events.rows))
        self.assertEqual(g.GroqReasoning(a.Events(),transport=a.fake_transport(a.answer())).reasoning_effort,'low')
        with self.assertRaises(ValueError):g.GroqReasoning(a.Events(),transport=a.fake_transport(a.answer()),reasoning_effort='invalid')

class PlanningRecoveryTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=f.MCPTests.asyncSetUp
    asyncTearDown=f.MCPTests.asyncTearDown
    cfg=f.MCPTests.cfg
    async def test_rejected_generated_tool_can_recover_on_new_user_request_without_retry_or_effect(self):
        async with f.Registry(self.events,self.cfg('extension')) as r:
            r.budget=f.FixtureBudget(0);r.journal=Journal(self.folder/'journal.jsonl');r.allowed_actions=[]
            model=recovery.FaultModel(g.ProviderError(400,'tool_use_failed'),f.decision(text='A coherent new answer.'))
            router=Router(model,r,self.events);first=await router.answer(f.history('A task'))
            self.assertIn('No new action',first);self.assertEqual(len(model.seen),1);self.assertEqual(r.calls,0)
            self.assertEqual(await router.answer(f.history('New topic')),'A coherent new answer.');self.assertEqual(r.calls,0)
    async def test_unclassified_bad_request_and_quota_still_fail_closed(self):
        async with f.Registry(self.events,self.cfg('extension')) as r:
            r.budget=f.FixtureBudget(0);r.journal=Journal(self.folder/'journal.jsonl');r.allowed_actions=[]
            for error in (g.ProviderError(400,'invalid_request_error'),g.ProviderError(400,'unclassified'),g.ProviderError(429)):
                model=recovery.FaultModel(error);router=Router(model,r,self.events)
                with self.assertRaises(g.ProviderError):await router.answer(f.history('A task'))
                self.assertEqual(len(model.seen),1);self.assertEqual(r.calls,0)

if __name__=='__main__':unittest.main()
