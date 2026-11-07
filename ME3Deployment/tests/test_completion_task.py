import unittest
from types import SimpleNamespace
from me3_completion.task import ConversationReasoner
from task_mcp.store import state_digest
from tests.test_completion import Events
class Tasks:
    def __init__(self):self.calls=[];self.row=None
    async def call(self,tool,g,**kw):
        self.calls.append(tool)
        if tool=='prepare_item':
            self.row={'operation_id':kw['operation_id'],'item':kw['item'],'quantity':kw['quantity'],'created_utc':'synthetic'}
            return {**self.row,'token':'synthetic-token'}
        if tool=='commit_item':return {'ok':True}
        if tool=='read_item':return {'row':self.row,'state_digest':state_digest(self.row),'verification_source':'fresh_readonly_sqlite_connection'}
class TaskTests(unittest.IsolatedAsyncioTestCase):
    def make(self):
        class Ordinary:
            async def answer(self,*args):raise AssertionError('unexpected text provider')
        tasks=Tasks();c=ConversationReasoner(Ordinary(),tasks,Events());c.controller=SimpleNamespace(clock=SimpleNamespace(current=1),current=lambda g:True)
        return c,tasks
    def user(self,text):return {'type':'user_input','content':[{'type':'text','text':text}]}
    async def test_revised_quantity_is_new_proposal_and_requires_completed_new_prompt(self):
        c,tasks=self.make();original=await c.answer([self.user('Add three markers to my shopping list')]);new=await c.answer([self.user('four markers')])
        self.assertIn('four markers',new);self.assertNotEqual(original,new);self.assertNotIn('commit_item',tasks.calls)
        reply=await c.answer([self.user('Confirm')]);self.assertEqual(reply,new);self.assertNotIn('commit_item',tasks.calls)
        reply=await c.answer([{'type':'model_output','content':[{'type':'text','text':new}]},self.user('Confirm')])
        self.assertIn('saved and independently checked four markers',reply);self.assertEqual(tasks.calls.count('commit_item'),1);self.assertEqual(tasks.calls[-1],'read_item')
    async def test_incomplete_and_canceled_task_never_commit(self):
        c,tasks=self.make();self.assertIn('What item',await c.answer([self.user('Add to my shopping list')]))
        self.assertFalse(tasks.calls);await c.answer([self.user('four markers')]);await c.answer([self.user('Cancel')]);self.assertIsNone(c.pending);self.assertNotIn('commit_item',tasks.calls)
    async def test_stop_is_not_acknowledgment_or_task_authorization(self):
        c,tasks=self.make();c.pending={'phase':'confirm'};self.assertEqual(await c.answer([self.user('Stop')]),'Stopped.');self.assertIsNone(c.pending);self.assertFalse(tasks.calls)
