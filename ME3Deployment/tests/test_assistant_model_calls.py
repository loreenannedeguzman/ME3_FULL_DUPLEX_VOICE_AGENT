"""Bounded ordered tool batches, identities and JSON integers; no providers."""
import json,math,unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from google.genai import types
from me3_assistant import groq_backend as g
from me3_assistant.router import Router
from me3_assistant.actions import Journal
from me3_assistant.music import intent,fingerprint
from tests import test_assistant_mode as f
from tests import test_assistant_workflow as w
from tests import test_assistant_groq_adapter as a

def goal_call(identifier,steps):return {'id':identifier,'type':'function','function':{'name':'run_goal_steps','arguments':json.dumps({'steps':steps})}}
def item(name,quantity):return {'tool':name,'arguments':{'item':'notebooks' if name=='propose_shopping_item' else 'pens','quantity':quantity}}

class ModelCallTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=f.MCPTests.asyncSetUp
    asyncTearDown=f.MCPTests.asyncTearDown
    cfg=w.GoalTests.cfg
    router=w.GoalTests.router
    rows=w.GoalTests.rows
    async def test_two_goal_calls_form_one_queue_with_separate_confirmations_and_no_extra_model_call(self):
        async with f.Registry(self.events,self.cfg('shopping')) as r:
            result=g.decision(a.answer(calls=[goal_call('one',[item('propose_shopping_item',2)]),goal_call('two',[item('propose_second_item',3)])]))
            router,model=self.router(r,result,writes=2)
            first=await router.answer(f.history('Add notebooks then pens'))
            self.assertIn('Step 1 of 2',first);self.assertEqual(self.rows(),[])
            second=await router.answer(f.history('Confirm',first));self.assertIn('Step 2 of 2',second);self.assertEqual(self.rows(),[('notebooks',2)])
            final=await router.answer(f.history('Confirmed',second));self.assertIn('2 notebooks',final);self.assertIn('3 pens',final)
            self.assertEqual(self.rows(),[('notebooks',2),('pens',3)]);self.assertEqual(len(model.seen),1);self.assertEqual(r.budget.used,2)
            event=next(e for e in self.events.rows if e['event']=='assistant_goal_calls_coalesced');self.assertEqual((event['calls'],event['steps'],event['execution_started']),(2,2,False))
    async def test_invalid_later_batched_goal_prevents_every_rpc(self):
        async with f.Registry(self.events,self.cfg('shopping')) as r:
            result=g.decision(a.answer(calls=[goal_call('one',[item('propose_shopping_item',2)]),goal_call('two',[item('propose_second_item',True)])]))
            router,_=self.router(r,result,writes=2)
            self.assertIn('safely',await router.answer(f.history('A goal')));self.assertEqual(r.calls,0);self.assertEqual(self.rows(),[])
    async def test_duplicate_effects_or_combined_size_over_six_are_rejected_before_dispatch(self):
        async with f.Registry(self.events,self.cfg('shopping')) as r:
            for calls in ([goal_call('one',[item('propose_shopping_item',2)]),goal_call('two',[item('propose_shopping_item',2)])],[goal_call('one',[item('propose_shopping_item',i) for i in range(1,5)]),goal_call('two',[item('propose_second_item',i) for i in range(1,5)])]):
                router,_=self.router(r,g.decision(a.answer(calls=calls)),writes=2)
                self.assertIn('safely',await router.answer(f.history('A goal')));self.assertEqual(r.calls,0)
    async def test_same_read_tool_twice_has_unambiguous_actual_results_and_response_ids(self):
        async with f.Registry(self.events,self.cfg()) as r:
            r.budget=f.FixtureBudget(0);r.journal=Journal(self.folder/'journal.jsonl');r.allowed_actions=[]
            calls=[{'id':identifier,'type':'function','function':{'name':'configured__calculate_expression','arguments':json.dumps({'expression':expression})}} for identifier,expression in (('one','2+2'),('two','3+3'))]
            t=a.fake_transport(None);t.request.side_effect=[a.answer(calls=calls),a.answer('Both calculations completed.')]
            router=Router(g.GroqReasoning(self.events,transport=t),r,self.events)
            reply=await router.answer(f.history('Calculate two sums'))
            self.assertIn('4',reply);self.assertIn('6',reply);self.assertEqual(r.calls,2)
            tools=[m for m in t.request.call_args.kwargs['json']['messages'] if m['role']=='tool']
            self.assertEqual([m['tool_call_id'] for m in tools],['one','two'])
            self.assertEqual([json.loads(m['content'])['result']['result'] for m in tools],['4','6'])
    async def test_decoder_failure_emits_only_a_known_validation_code(self):
        t=a.fake_transport(a.answer(calls=[goal_call('duplicate',[]),goal_call('duplicate',[])]));model=g.GroqReasoning(self.events,transport=t)
        with self.assertRaisesRegex(ValueError,'invalid_candidate_tool_call'):await model.decide([],[],[])
        event=next(e for e in self.events.rows if e['event']=='groq_decision_rejected')
        self.assertEqual(event['validation_code'],'invalid_candidate_tool_call');self.assertFalse(event['argument_values_retained']);self.assertEqual(t.request.await_count,1)

class IntegerTests(unittest.TestCase):
    def test_integral_json_volume_has_the_same_value_and_digest_without_rounding(self):
        for command,track in (('play','Study.wav'),('volume',None)):
            self.assertEqual(intent(command,track,20.0),intent(command,track,20))
            self.assertEqual(fingerprint(intent(command,track,20.0)),fingerprint(intent(command,track,20)))
        for value in (True,20.5,float('nan'),float('inf'),-1.0,61.0):
            with self.subTest(value=value),self.assertRaises(ValueError):intent('volume',None,value)
    def test_duplicate_or_excess_call_ids_and_unknown_response_id_fail_closed(self):
        for calls in ([goal_call('same',[])]*2,[goal_call(str(i),[]) for i in range(4)]):
            with self.assertRaises(ValueError):g.decision(a.answer(calls=calls))
        content,_,_=g.decision(a.answer(calls=[a.call()]))
        response=types.Content(role='user',parts=[types.Part(function_response=types.FunctionResponse(id='wrong',name='fixture',response={'result':4}))])
        with self.assertRaisesRegex(ValueError,'ambiguous'):g.messages([],[content,response])

if __name__=='__main__':unittest.main()
