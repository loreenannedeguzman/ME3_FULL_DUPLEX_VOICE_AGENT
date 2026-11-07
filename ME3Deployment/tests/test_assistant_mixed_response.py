"""Mixed conversational/tool requests must retain both parts without new calls."""
import json,unittest
from tests import test_assistant_mode as f
from tests import test_assistant_result_delivery as readback
from me3_assistant.result_delivery import deliver_results,stated_result
from me3_assistant.actions import Journal
from me3_assistant.router import Router
from me3_assistant.groq_backend import GroqReasoning
from types import SimpleNamespace
from unittest.mock import AsyncMock

class MixedReadbackTests(unittest.TestCase):
    def test_one_calculator_tool_does_not_erase_conversational_answer(self):
        r,k,d=readback.ReadbackBoundsTests().values();explanation='Gravity pulls heavy droplets toward the ground.'
        answer,n=deliver_results(r,{k:d},explanation)
        self.assertIn(explanation,answer);self.assertIn('result is 4',answer);self.assertEqual(n,1)
    def test_equation_result_and_context_are_already_complete(self):
        r,k,d=readback.ReadbackBoundsTests().values();reply='Gravity pulls the droplets down. 2+2=4.'
        self.assertEqual(deliver_results(r,{k:d},reply),(reply,0))
    def test_wrong_pure_arithmetic_fragment_does_not_replace_or_pollute_context(self):
        r,k,d=readback.ReadbackBoundsTests().values();reply='Gravity pulls the droplets down. Two plus two is five.'
        answer,n=deliver_results(r,{k:d},reply)
        self.assertIn('Gravity pulls the droplets down.',answer);self.assertIn('result is 4',answer);self.assertNotIn('is five',answer)
    def test_number_in_input_without_equality_still_not_a_reported_result(self):
        self.assertFalse(stated_result('I calculated 4 plus 0.','4'));self.assertTrue(stated_result('4+0 = 4.','4'))
    def test_decimal_equation_and_nonmath_list_prose(self):
        r,k,d=readback.ReadbackBoundsTests().values('1/4','0.25');reply='Gravity pulls droplets downward. 1/4=0.25.'
        self.assertEqual(deliver_results(r,{k:d},reply),(reply,0))
        answer,_=deliver_results(r,{k:d},'Two notebooks and four pens.');self.assertIn('Two notebooks and four pens.',answer)

class ActualGroqRouterFixtureTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=f.MCPTests.asyncSetUp
    asyncTearDown=f.MCPTests.asyncTearDown
    cfg=f.MCPTests.cfg
    async def test_actual_mcp_math_result_preserves_groq_context_without_extra_round(self):
        first={'choices':[{'message':{'content':None,'tool_calls':[{'id':'mixed_fixture','type':'function','function':{'name':'configured__calculate_expression','arguments':'{"expression":"2+2"}'}}]}}]}
        final={'choices':[{'message':{'content':'Gravity pulls the droplets down. 2+2=4.'}}]}
        transport=SimpleNamespace(request=AsyncMock(side_effect=[first,final]),close=AsyncMock())
        model=GroqReasoning(self.events,transport=transport)
        async with f.Registry(self.events,self.cfg()) as registry:
            registry.budget=f.FixtureBudget(0);registry.journal=Journal(self.folder/'journal.jsonl');registry.allowed_actions=[]
            router=Router(model,registry,self.events)
            answer=await router.answer(f.history('What makes those droplets fall, and calculate two plus two?'))
            self.assertEqual(answer,final['choices'][0]['message']['content']);self.assertEqual(registry.calls,1);self.assertEqual(transport.request.await_count,2)
        await model.close()

if __name__=='__main__':unittest.main()
