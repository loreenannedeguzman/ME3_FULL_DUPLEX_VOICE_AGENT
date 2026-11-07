"""Omitted tool-result regression with real local MCP; no provider/Piper."""
import asyncio,json,unittest
from types import SimpleNamespace
from tests import test_assistant_mode as f
from me3_assistant.actions import Journal
from me3_assistant.router import Router
from me3_assistant.result_delivery import deliver_results

class ResultDeliveryTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=f.MCPTests.asyncSetUp
    asyncTearDown=f.MCPTests.asyncTearDown
    cfg=f.MCPTests.cfg
    def router(self,r,*answers):
        r.budget=f.FixtureBudget(0);r.journal=Journal(self.folder/'journal.jsonl');r.allowed_actions=[]
        model=f.Model(*answers);return Router(model,r,self.events),model
    async def test_actual_MCP_result_not_just_echoed_expression_reaches_final_answer(self):
        async with f.Registry(self.events,self.cfg()) as r:
            router,model=self.router(r,f.decision('configured__calculate_expression',{'expression':'2+2'}),f.decision(text='Two plus two is calculated as two plus two.'))
            answer=await router.answer(f.history('What is two plus two?'))
            self.assertIn('result is 4',answer);self.assertNotIn('calculated as',answer)
            self.assertEqual(r.calls,1);self.assertEqual(len(model.seen),2)
            self.assertTrue(any(e['event']=='assistant_result_readback' for e in self.events.rows))
    async def test_complete_word_result_and_ordinary_conversation_remain_unchanged(self):
        async with f.Registry(self.events,self.cfg()) as r:
            router,_=self.router(r,f.decision('configured__calculate_expression',{'expression':'2+2'}),f.decision(text='Two plus two is four.'),f.decision(text='A clear explanation.'))
            self.assertEqual(await router.answer(f.history('Calculate')),'Two plus two is four.')
            self.assertEqual(await router.answer(f.history('Explain')),'A clear explanation.');self.assertEqual(r.calls,1)
    async def test_value_only_in_expression_is_not_a_reported_result(self):
        async with f.Registry(self.events,self.cfg()) as r:
            router,_=self.router(r,f.decision('configured__calculate_expression',{'expression':'2+0'}),f.decision(text='I calculated 2 plus 0.'))
            self.assertIn('result is 2',await router.answer(f.history('Calculate')))
    async def test_multiple_calculations_keep_each_missing_result_without_duplicate_RPC(self):
        async with f.Registry(self.events,self.cfg()) as r:
            calls=[f.types.FunctionCall(name='configured__calculate_expression',args={'expression':v}) for v in ('5-5','1/4')]
            content=f.types.Content(role='model',parts=[f.types.Part(function_call=c) for c in calls])
            router,_=self.router(r,(content,calls,''),f.decision(text='The calculations are done.'))
            answer=await router.answer(f.history('Calculate both'))
            self.assertIn('result is 0.',answer);self.assertIn('result is 0.25.',answer);self.assertEqual(r.calls,2)
    async def test_tool_error_still_uses_truthful_failure_without_fabricated_result(self):
        async with f.Registry(self.events,self.cfg()) as r:
            router,model=self.router(r,f.decision('configured__calculate_expression',{'expression':'1/0'}))
            self.assertIn('could not finish',await router.answer(f.history('divide')));self.assertEqual(len(model.seen),1)
    async def test_superseded_final_model_reply_cannot_deliver_old_result(self):
        async with f.Registry(self.events,self.cfg()) as r:
            router,model=self.router(r,f.decision('configured__calculate_expression',{'expression':'2+2'}))
            active=[True];router.controller=SimpleNamespace(clock=SimpleNamespace(current=1),current=lambda g:active[0]);decide=model.decide
            async def late(h,d,c):
                if c:active[0]=False;return f.decision(text='Calculated.')
                return await decide(h,d,c)
            model.decide=late
            self.assertEqual(await router.answer(f.history('Calculate')),'');self.assertEqual(r.calls,1)
            self.assertFalse(any(e['event']=='assistant_result_readback' for e in self.events.rows))

class ReadbackBoundsTests(unittest.TestCase):
    def values(self,expression='2+2',result='4'):
        r=SimpleNamespace(config={'servers':[{'id':'u','module':'me3_assistant.utility_server','identity':'me3'}]},reads={'u__calc':('u','calculate_expression'),'u__list':('u','list_items')})
        key=json.dumps(['u__calc',{'expression':expression}]);data={'ok':True,'read_only':True,'expression':expression,'result':result}
        return r,key,data
    def test_mixed_requests_preserve_list_readback(self):
        r,k,d=self.values();text='Two notebooks, two pens and one paperclip.'
        answer,n=deliver_results(r,{k:d,json.dumps(['u__list',{}]):{'rows':[]}},text)
        self.assertIn('result is 4',answer);self.assertIn(text,answer);self.assertEqual(n,1)
    def test_arbitrary_extension_result_cannot_be_promoted_to_owned_calculator_fact(self):
        r,k,d=self.values();r.config['servers'][0]['module']='tests.assistant_fixture_server'
        self.assertEqual(deliver_results(r,{k:d},'Ordinary reply.'),('Ordinary reply.',0))
    def test_calculator_fallback_retains_an_already_reported_result_as_well(self):
        r,k,d=self.values();second=json.dumps(['u__calc',{'expression':'3+3'}])
        answer,n=deliver_results(r,{k:d,second:{**d,'expression':'3+3','result':'6'}},'Two plus two is four; I also did the other calculation.')
        self.assertIn('result is 4.',answer);self.assertIn('result is 6.',answer);self.assertEqual(n,1)
    def test_invalid_mismatched_failed_and_nonfinite_results_not_reported_as_success(self):
        for patch in ({'ok':False},{'expression':'5+5'},{'result':'NaN'},{'result':'1e999'},{'result':4},{'read_only':False}):
            r,k,d=self.values();d.update(patch)
            with self.subTest(patch=patch):self.assertEqual(deliver_results(r,{k:d},'Reply.'),('Reply.',0))
    def test_long_mixed_reply_never_clips_the_numeric_result_or_exceeds_bound(self):
        r,k,d=self.values('1/4','0.25');answer,n=deliver_results(r,{k:d,json.dumps(['u__list',{}]):{}},'Long detail. '*100)
        self.assertLessEqual(len(answer),650);self.assertIn('0.25.',answer);self.assertIn('separately',answer)
