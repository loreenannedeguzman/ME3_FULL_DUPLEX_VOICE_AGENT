import unittest
from me3_core.task import CoreTaskReasoner,check_trial_proposal,ProposalMismatch
from task_mcp.client import TaskError
class Events:
    def __init__(self):self.rows=[]
    def emit(self,event,**fields):self.rows.append({'event':event,**fields})
def history(text):return [{'content':[{'text':text}]}]
class CoreTaskTests(unittest.IsolatedAsyncioTestCase):
    async def test_four_rows_are_spoken_and_all_validated(self):
        class Tasks:
            async def call(self,*a,**kw):return {'rows':[{'item':x,'quantity':q} for x,q in [('notebooks',2),('pens',2),('pencils',5),('erasers',3)]],'verification_source':'fresh_readonly_sqlite_connection'}
        e=Events();reasoner=CoreTaskReasoner(None,Tasks(),e);text=await reasoner.answer(history('Read my shopping list'))
        for value in ['two notebooks','two pens','five pencils','three erasers']:self.assertIn(value,text)
        self.assertFalse(reasoner.failed.is_set());self.assertEqual(e.rows[-1]['spoken_count'],4)
    async def test_maximum_list_is_bounded_and_excess_truthful(self):
        class Tasks:
            async def call(self,*a,**kw):return {'rows':[{'item':'a'*60,'quantity':20} for _ in range(100)],'verification_source':'fresh_readonly_sqlite_connection'}
        e=Events();reasoner=CoreTaskReasoner(None,Tasks(),e);text=await reasoner.answer(history('Read my shopping list'))
        self.assertLessEqual(len(text),700);self.assertIn('92 more items',text)
    async def test_malformed_row_or_unverified_data_never_claims_success(self):
        class Tasks:
            async def call(self,*a,**kw):return {'rows':[{'item':'pens','quantity':True}],'verification_source':'fresh_readonly_sqlite_connection'}
        reasoner=CoreTaskReasoner(None,Tasks(),Events());text=await reasoner.answer(history('Read my shopping list'))
        self.assertTrue(reasoner.failed.is_set());self.assertIn('could not verify',text)
    async def test_trial_mismatch_asks_for_detail_without_write_or_task_failure(self):
        class Tasks:
            async def call(self,*a,**kw):raise ProposalMismatch('test_only')
        e=Events();reasoner=CoreTaskReasoner(None,Tasks(),e);text=await reasoner.answer(history('Add three erasers to my shopping list'))
        self.assertFalse(reasoner.failed.is_set());self.assertEqual(reasoner.pending,{'phase':'item'});self.assertIn('Nothing has been saved',text)
    async def test_other_task_errors_still_stop(self):
        class Tasks:
            async def call(self,*a,**kw):raise TaskError('real_failure')
        reasoner=CoreTaskReasoner(None,Tasks(),Events());await reasoner.answer(history('Add three erasers to my shopping list'))
        self.assertTrue(reasoner.failed.is_set())
    async def test_trial_guard_is_strict_and_logs_only_comparisons(self):
        e=Events();check_trial_proposal(e,1,item='erasers',quantity=3)
        for item,q in [('eraser',3),('razors',3),('erasers',True),('erasers',2)]:
            with self.assertRaises(ProposalMismatch):check_trial_proposal(e,2,item=item,quantity=q)
        self.assertTrue(all('item' not in x and 'quantity' not in x for x in e.rows))

    async def test_partial_task_syntax_asks_detail_without_inventing_or_calling_tool(self):
        class Tasks:
            async def call(self,*a,**kw):raise AssertionError('No tool before essential detail and confirmation')
        for request in ['Add three erasers my shopping list','Please put three erasers in my shopping list']:
            reasoner=CoreTaskReasoner(None,Tasks(),Events());text=await reasoner.answer(history(request))
            self.assertIn('What item, and how many',text);self.assertEqual(reasoner.pending,{'phase':'item'});self.assertFalse(reasoner.failed.is_set())
    async def test_unrelated_or_negated_action_is_not_reinterpreted(self):
        class Ordinary:
            async def answer(self,h):return 'ordinary'
        for request in ['Do not add three erasers to my shopping list','What is on a shopping list','Add two plus two']:
            reasoner=CoreTaskReasoner(Ordinary(),None,Events());self.assertEqual(await reasoner.answer(history(request)),'ordinary');self.assertIsNone(reasoner.pending)
    async def test_task_only_harness_returns_clarification_without_provider_or_exception(self):
        from me3_core.task import task_only_clarification
        e=Events();text=task_only_clarification(e,1)
        self.assertIn('Nothing has been saved',text);self.assertFalse(e.rows[-1]['provider_called'])
