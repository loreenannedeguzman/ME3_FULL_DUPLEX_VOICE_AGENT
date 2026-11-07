"""Conversation UI state comes from real events; presentation has no effects."""
import asyncio,unittest
from unittest.mock import patch
from me3_assistant.gui import Console,row
from tests.test_assistant_mode import config

class DeployedConsoleTests(unittest.IsolatedAsyncioTestCase):
    def console(self):
        cfg=config('extension');cfg['sha256']='fixture';return Console(cfg,{'revision':'fixture'})
    async def test_task_rpc_result_does_not_claim_verified_external_action(self):
        c=self.console();c.observe('assistant_rpc_start',request_id='private-id',tool='calculate_expression',arguments={'secret':'hidden'})
        c.observe('assistant_rpc_end',request_id='private-id',tool='calculate_expression')
        self.assertEqual(c.tasks['private-id']['state'],'received')
        self.assertNotIn('hidden',str(c.tasks));self.assertNotIn('private-id',str(list(c.tasks.values())))
        c.observe('assistant_goal_progress',goal_id='goal',tool='propose_item',state='uncertain',ordinal=2)
        self.assertEqual(c.tasks['goal']['state'],'uncertain')
    async def test_start_retains_conversation_without_starting_audio_in_fixture(self):
        c=self.console();c.history=[row('user','Remember this topic.'),row('assistant','Understood.')]
        async def hold():await c.stop_event.wait()
        with patch.object(c,'gate',return_value=({'backend':'google'},{})),patch('me3_assistant.backends.check_ready'),patch.object(c,'text',new=lambda *args:hold()):
            await c.command({'action':'start_text'});await asyncio.sleep(0)
            self.assertEqual(len(c.history),2);await c.stop()
        self.assertEqual(len(c.history),2)
    async def test_clear_drops_only_in_memory_conversation_and_task_view(self):
        c=self.console();c.history=[row('user','hello')];c.tasks={'a':{'state':'received'}}
        await c.command({'action':'clear'});self.assertEqual(c.history,[]);self.assertEqual(c.tasks,{})

if __name__=='__main__':unittest.main()
