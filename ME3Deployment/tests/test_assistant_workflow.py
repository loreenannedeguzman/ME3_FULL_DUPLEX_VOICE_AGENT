"""Real fixture MCP goal execution; no providers, synthesis or live task writes."""
import asyncio,contextlib,copy,json,sqlite3,unittest
from types import SimpleNamespace
from unittest.mock import patch
from google.genai import types
from tests import test_assistant_mode as f
from me3_assistant.router import Router
from me3_assistant.actions import Journal
from me3_assistant.workflow import NAME

def plan(*steps):return f.decision(NAME,{'steps':[{'tool':name,'arguments':args} for name,args in steps]})

class GoalTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=f.MCPTests.asyncSetUp
    asyncTearDown=f.MCPTests.asyncTearDown
    def cfg(self,kind='utility'):
        cfg=f.config(kind,self.folder/'test.sqlite3')
        if kind=='shopping':
            # Two independently bounded MCP participants: each retains its
            # production one-write-per-process limit. Only a temporary DB is used.
            second=copy.deepcopy(cfg['servers'][0]);second.update(id='second',read_tools=[])
            cfg['servers'].append(second)
            cfg['actions'].append({'name':'propose_second_item','server':'second','adapter':'shopping_item'})
        path=self.folder/'config.json';path.write_text(json.dumps(cfg));return f.load(path)
    def router(self,r,*answers,writes=0):
        r.budget=f.FixtureBudget(writes);r.journal=Journal(self.folder/'journal.jsonl')
        r.allowed_actions=['propose_shopping_item','propose_second_item'] if writes else []
        model=f.Model(*answers);return Router(model,r,self.events),model
    def rows(self):
        with contextlib.closing(sqlite3.connect(self.folder/'test.sqlite3')) as c:return c.execute('SELECT item,quantity FROM items ORDER BY created_utc,operation_id').fetchall()
    def shopping_plan(self):return plan(('propose_shopping_item',{'item':'notebooks','quantity':2}),('propose_second_item',{'item':'pens','quantity':3}),('configured__list_items',{}))

    async def test_read_goal_runs_real_tools_and_returns_evidence_without_value_omission(self):
        async with f.Registry(self.events,self.cfg()) as r:
            router,m=self.router(r,plan(('configured__calculate_expression',{'expression':'2+2'}),('configured__get_current_time',{'utc_offset_minutes':480})),f.decision(text='The clock was read.'))
            reply=await router.answer(f.history('Calculate and get Manila time'))
            self.assertIn('result is 4',reply);self.assertEqual(r.calls,2)
            result=m.seen[1][2][-1].parts[0].function_response.response['result']
            self.assertEqual(result['status'],'completed');self.assertTrue(all(s['verification']=='tool_response_only' for s in result['steps']))
            self.assertEqual(result['steps'][0]['result']['result'],'4')
    async def test_invalid_later_schema_prevents_every_earlier_step(self):
        async with f.Registry(self.events,self.cfg()) as r:
            router,m=self.router(r,plan(('configured__calculate_expression',{'expression':'2+2'}),('configured__get_current_time',{'utc_offset_minutes':True})))
            self.assertIn('safely',await router.answer(f.history('A goal')));self.assertEqual(r.calls,0)
    async def test_unknown_hidden_action_and_nested_goals_are_not_permitted(self):
        async with f.Registry(self.events,self.cfg()) as r:
            for bad in ('commit_item',NAME,'cancel_pending_action','unregistered__shell'):
                router,m=self.router(r,plan(('configured__calculate_expression',{'expression':'2+2'}),(bad,{})))
                self.assertIn('safely',await router.answer(f.history('A goal')));self.assertEqual(r.calls,0)
    async def test_duplicate_goal_action_is_rejected_before_dispatch(self):
        async with f.Registry(self.events,self.cfg('shopping')) as r:
            router,m=self.router(r,plan(*[('propose_shopping_item',{'item':'notebooks','quantity':2})]*2),writes=2)
            self.assertIn('safely',await router.answer(f.history('A goal')));self.assertEqual(r.calls,0)
    async def test_two_effects_each_need_delivered_confirmation_and_independent_readback(self):
        async with f.Registry(self.events,self.cfg('shopping')) as r:
            router,m=self.router(r,self.shopping_plan(),f.decision(text='The saved list was read.'),writes=2)
            first=await router.answer(f.history('Save notebooks and pens then read the list'))
            self.assertIn('Step 1 of 3',first);self.assertEqual(self.rows(),[]);self.assertEqual(r.calls,1)
            second=await router.answer(f.history('confirmed',first))
            self.assertIn('independently checked',second);self.assertIn('Step 2 of 3',second)
            self.assertEqual(self.rows(),[('notebooks',2)]);self.assertEqual(r.calls,4)
            final=await router.answer(f.history('confirm',second))
            self.assertIn('saved list',final);self.assertEqual(self.rows(),[('notebooks',2),('pens',3)])
            self.assertEqual(r.budget.used,2);self.assertEqual(r.calls,7)
            result=m.seen[-1][2][-1].parts[0].function_response.response['result']
            self.assertEqual([s['status'] for s in result['steps']],['verified','verified','read_received'])
            self.assertEqual([s['verification'] for s in result['steps']],['independent_readback','independent_readback','tool_response_only'])
            self.assertEqual(len([e for e in self.events.rows if e['event']=='assistant_action_verified']),2)
            self.assertIn('no pending',await router.answer(f.history('confirm',final)));self.assertEqual(r.budget.used,2)
    async def test_partly_spoken_or_uncertain_prompt_never_authorizes_a_goal_effect(self):
        async with f.Registry(self.events,self.cfg('shopping')) as r:
            router,m=self.router(r,self.shopping_plan(),writes=2)
            prompt=await router.answer(f.history('A goal'))
            self.assertEqual(await router.answer(f.history('confirm',prompt,'cancelled')),prompt)
            self.assertEqual(await router.answer(f.history('Confirm?',prompt)),prompt)
            self.assertEqual(self.rows(),[]);self.assertEqual(r.budget.used,0)
    async def test_cancel_after_one_verified_step_keeps_it_and_never_runs_the_rest(self):
        async with f.Registry(self.events,self.cfg('shopping')) as r:
            router,m=self.router(r,self.shopping_plan(),writes=2)
            first=await router.answer(f.history('A goal'));second=await router.answer(f.history('confirm',first))
            self.assertIn('Canceled',await router.answer(f.history('cancel',second)))
            self.assertEqual(router.workflow.states,['verified','canceled','not_run'])
            self.assertIn('no pending',await router.answer(f.history('confirm',second)))
            self.assertEqual(self.rows(),[('notebooks',2)]);self.assertEqual(r.budget.used,1)
    async def test_replacement_request_discards_old_queue_and_confirmation(self):
        async with f.Registry(self.events,self.cfg('shopping')) as r:
            router,m=self.router(r,self.shopping_plan(),f.decision(text='The replacement answer.'),writes=2)
            prompt=await router.answer(f.history('A goal'));old=router.workflow
            self.assertEqual(await router.answer(f.history('Instead explain gravity',prompt)),'The replacement answer.')
            self.assertEqual(old.states,['canceled','not_run','not_run']);self.assertIsNone(router.pending)
            self.assertEqual(self.rows(),[]);self.assertEqual(r.budget.used,0)
    async def test_actual_tool_failure_retains_successful_result_and_stops_later_steps(self):
        async with f.Registry(self.events,self.cfg()) as r:
            router,m=self.router(r,plan(('configured__calculate_expression',{'expression':'2+2'}),('configured__calculate_expression',{'expression':'1/0'}),('configured__get_current_time',{'utc_offset_minutes':480})))
            reply=await router.answer(f.history('A goal'))
            self.assertIn('stopped at step 2',reply);self.assertIn('result is 4',reply)
            self.assertEqual(router.workflow.states,['read_received','failed','not_run'])
            self.assertEqual(r.calls,2);self.assertEqual(len(m.seen),1)
    async def test_canceled_MCP_step_never_runs_later_steps_or_emits_a_spoken_result(self):
        async with f.Registry(self.events,self.cfg('extension')) as r:
            router,m=self.router(r,plan(('configured__delayed_label',{'label':'fixture'}),('configured__count_words',{'text':'later work'})))
            task=asyncio.create_task(router.answer(f.history('A goal')))
            for _ in range(100):
                if r.calls:break
                await asyncio.sleep(.01)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
            self.assertEqual(r.calls,1);self.assertEqual(router.workflow.states,['canceled','not_run'])
    async def test_uncertain_commit_outcome_blocks_remaining_actions_and_never_retries(self):
        async with f.Registry(self.events,self.cfg('shopping')) as r:
            router,m=self.router(r,self.shopping_plan(),writes=2)
            first=await router.answer(f.history('A goal'))
            async def missing(*args):raise ValueError('fixture_unverified')
            router.actions.adapters['propose_shopping_item'].verify=missing
            reply=await router.answer(f.history('confirm',first))
            self.assertIn('could not verify',reply);self.assertIn('not run',reply)
            self.assertEqual(router.workflow.states,['uncertain','not_run','not_run']);self.assertEqual(self.rows(),[('notebooks',2)])
            self.assertIn('no pending',await router.answer(f.history('confirm',reply)));self.assertEqual(r.budget.used,1)
    async def test_next_action_budget_failure_still_reports_the_prior_verified_action(self):
        async with f.Registry(self.events,self.cfg('shopping')) as r:
            router,m=self.router(r,self.shopping_plan(),writes=1)
            prompt=await router.answer(f.history('A goal'))
            reply=await router.answer(f.history('confirm',prompt))
            self.assertIn('saved and independently checked 2 notebooks',reply)
            self.assertIn('not authorized',reply);self.assertIn('not run',reply)
            self.assertEqual(self.rows(),[('notebooks',2)]);self.assertEqual(r.budget.used,1)
    async def test_summary_timeout_cannot_hide_verified_effects_or_retry_them(self):
        from tests.test_assistant_recovery import FaultModel
        async with f.Registry(self.events,self.cfg('shopping')) as r:
            router,m=self.router(r,writes=1)
            router.ordinary=FaultModel(plan(('propose_shopping_item',{'item':'notebooks','quantity':2}),('configured__list_items',{})),TimeoutError())
            prompt=await router.answer(f.history('A goal'))
            reply=await router.answer(f.history('confirm',prompt))
            self.assertIn('1 actions were independently checked',reply);self.assertIn('could not finish',reply)
            self.assertEqual(self.rows(),[('notebooks',2)]);self.assertEqual(r.budget.used,1)
    async def test_expired_proposal_stops_the_queue_without_any_commit(self):
        async with f.Registry(self.events,self.cfg('shopping')) as r:
            router,m=self.router(r,self.shopping_plan(),writes=2)
            prompt=await router.answer(f.history('A goal'));router.pending['expires']=0
            self.assertIn('expired',await router.answer(f.history('confirm',prompt)))
            self.assertEqual(self.rows(),[]);self.assertEqual(router.workflow.status,'failed')
    async def test_goal_duplicates_after_completion_reuse_results_without_reexecution(self):
        async with f.Registry(self.events,self.cfg()) as r:
            selected=plan(('configured__calculate_expression',{'expression':'2+2'}),('configured__get_current_time',{'utc_offset_minutes':480}))
            router,m=self.router(r,selected,selected,f.decision(text='The result is four.'))
            await router.answer(f.history('A goal'));self.assertEqual(r.calls,2)
    async def test_existing_read_is_reused_inside_a_later_goal(self):
        async with f.Registry(self.events,self.cfg()) as r:
            router,m=self.router(r,f.decision('configured__calculate_expression',{'expression':'2+2'}),plan(('configured__calculate_expression',{'expression':'2+2'}),('configured__get_current_time',{'utc_offset_minutes':480})),f.decision(text='The result is four.'))
            await router.answer(f.history('A goal'));self.assertEqual(r.calls,2)
    async def test_post_action_read_is_fresh_and_never_reuses_pre_action_state(self):
        async with f.Registry(self.events,self.cfg('shopping')) as r:
            router,m=self.router(r,plan(('configured__list_items',{}),('propose_shopping_item',{'item':'notebooks','quantity':2}),('configured__list_items',{})),f.decision(text='The new saved state was checked.'),writes=1)
            prompt=await router.answer(f.history('Read, add, and read again'))
            await router.answer(f.history('confirm',prompt))
            result=m.seen[-1][2][-1].parts[0].function_response.response['result']
            self.assertEqual(result['steps'][0]['result']['rows'],[])
            self.assertEqual(len(result['steps'][2]['result']['rows']),1);self.assertEqual(r.calls,5)
    async def test_superseded_read_cannot_advance_or_return_the_old_goal(self):
        async with f.Registry(self.events,self.cfg()) as r:
            router,m=self.router(r,plan(('configured__calculate_expression',{'expression':'2+2'}),('configured__get_current_time',{'utc_offset_minutes':480})))
            active=[True];router.controller=SimpleNamespace(clock=SimpleNamespace(current=1),current=lambda g:active[0])
            original=r.read
            async def stale(*args):result=await original(*args);active[0]=False;return result
            r.read=stale
            self.assertEqual(await router.answer(f.history('A goal')),'');self.assertEqual(r.calls,1)
    async def test_goal_and_another_call_in_one_batch_run_nothing(self):
        async with f.Registry(self.events,self.cfg()) as r:
            content,calls,_=plan(('configured__calculate_expression',{'expression':'2+2'}),('configured__get_current_time',{'utc_offset_minutes':480}))
            other=types.FunctionCall(name='configured__calculate_expression',args={'expression':'3+3'});calls.append(other);content.parts.append(types.Part(function_call=other))
            router,m=self.router(r,(content,calls,''))
            self.assertIn('safely',await router.answer(f.history('A goal')));self.assertEqual(r.calls,0)
    async def test_goal_progress_projection_contains_no_arguments_results_or_tokens(self):
        from me3_assistant.gui import Console
        async with f.Registry(self.events,self.cfg()) as r:
            router,m=self.router(r,plan(('configured__calculate_expression',{'expression':'2+2'}),('configured__get_current_time',{'utc_offset_minutes':480})),f.decision(text='Done.'))
            await router.answer(f.history('A goal'))
            progress=[e for e in self.events.rows if e['event']=='assistant_goal_progress']
            self.assertEqual(progress[-1]['state'],'completed');self.assertTrue(progress)
            c=Console(self.cfg(),{'revision':'fixture'})
            c.observe('assistant_goal_progress',tool='fixture',state='verified',ordinal=2,args='private',result='private',token='private')
            self.assertEqual(c.events[-1]['state'],'verified');self.assertNotIn('private',json.dumps(c.events))

if __name__=='__main__':unittest.main()

