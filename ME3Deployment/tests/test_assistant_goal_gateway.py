"""Actual MCP schemas and composed Groq controller flow; no hosted/audio calls."""
import asyncio,json,shutil,unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from jsonschema import Draft202012Validator
from me3_assistant.registry import Registry
from me3_assistant.router import Router
from me3_assistant.workflow import NAME
from me3_assistant.groq_backend import GroqReasoning,messages,ProviderError
from tests import test_assistant_music as m

def steps():return [{'tool':'propose_music_control','arguments':{'command':'play','track_id':'Study.wav','volume':20}},{'tool':'propose_music_control','arguments':{'command':'volume','volume':40}}]

class GoalGatewayTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=m.MusicMCPTests.asyncSetUp
    asyncTearDown=m.MusicMCPTests.asyncTearDown
    setup_actions=m.MusicMCPTests.setup_actions

    async def test_model_has_one_effect_gateway_and_typed_dynamic_action_ids(self):
        async with Registry(self.events,self.cfg,music_bus=self.bus.path) as r:
            self.setup_actions(r,2);model=m.Model(m.decision(text='Which track?'));router=Router(model,r,self.events)
            await router.answer(m.history('Play something'))
            declarations=model.seen[0][1];self.assertNotIn('propose_music_control',{d['name'] for d in declarations})
            gateway=next(d for d in declarations if d['name']==NAME);validator=Draft202012Validator(gateway['parameters'])
            validator.validate({'steps':steps()});validator.validate({'steps':[steps()[0]]})
            for bad in ([],steps()*4,[{'tool':'commit_music_control','arguments':{}}],[{'tool':'propose_music_control','arguments':{'command':'volume','track_id':'Study.wav','volume':40}}]):
                with self.subTest(bad=bad):self.assertFalse(validator.is_valid({'steps':bad}))
            self.assertEqual(r.calls,0);self.assertEqual(self.bus.snapshot()['state'],'stopped')

    async def test_volume_track_mismatch_in_later_step_blocks_every_earlier_rpc(self):
        async with Registry(self.events,self.cfg,music_bus=self.bus.path) as r:
            self.setup_actions(r,2);bad=steps();bad[1]['arguments']['track_id']='Study.wav'
            router=Router(m.Model(m.decision(NAME,{'steps':bad})),r,self.events)
            self.assertIn('safely',await router.answer(m.history('Play then raise volume')))
            self.assertEqual(r.calls,0);self.assertIsNone(router.pending)

    async def test_command_specific_schemas_match_effect_validation_boundaries(self):
        async with Registry(self.events,self.cfg,music_bus=self.bus.path) as r:
            a=self.setup_actions(r,2);v=Draft202012Validator(a.declarations()[0]['parameters'])
            for good in ({'command':'play','track_id':'Study.wav'},{'command':'volume','volume':40},{'command':'pause'}):self.assertTrue(v.is_valid(good))
            for bad in ({'command':'volume','track_id':'Study.wav','volume':40},{'command':'pause','volume':20},{'command':'play','track_id':None},{'command':'play','track_id':'Study dot wav'},{'command':'volume','volume':True},{'command':'volume','volume':61}):self.assertFalse(v.is_valid(bad))

    async def test_real_groq_payload_carries_nested_schemas_without_direct_effect_aliases(self):
        async with Registry(self.events,self.cfg,music_bus=self.bus.path) as r:
            self.setup_actions(r,2);transport=SimpleNamespace(request=AsyncMock(return_value={'choices':[{'message':{'content':'Which track?'}}]}),close=AsyncMock())
            router=Router(GroqReasoning(self.events,transport=transport),r,self.events)
            await router.answer(m.history('Play Study dot wav then set forty percent'))
            payload=transport.request.call_args.kwargs['json'];tools=payload['tools']
            self.assertNotIn('propose_music_control',{t['function']['name'] for t in tools})
            schema=next(t['function']['parameters'] for t in tools if t['function']['name']==NAME)
            Draft202012Validator(schema).validate({'steps':steps()});self.assertIn('Study.wav',json.dumps(schema));self.assertEqual(r.calls,0)

    @unittest.skipUnless(shutil.which('ffmpeg'),'FFmpeg required for actual temporary decoder')
    async def test_composed_groq_two_effects_confirm_verify_without_post_effect_model_request(self):
        async with Registry(self.events,self.cfg,music_bus=self.bus.path) as r:
            self.setup_actions(r,2)
            call={'id':'fixture_goal_1','type':'function','function':{'name':NAME,'arguments':json.dumps({'steps':steps()})}}
            transport=SimpleNamespace(request=AsyncMock(side_effect=[{'choices':[{'message':{'tool_calls':[call]}}]},ProviderError(429)]),close=AsyncMock())
            router=Router(GroqReasoning(self.events,transport=transport),r,self.events)
            first=await router.answer(m.history('Play Study then set forty percent'))
            self.assertIn('Step 1 of 2',first);self.assertEqual(self.bus.snapshot()['state'],'stopped')
            async def consume():
                while True:self.bus.mix(bytes(3840));await asyncio.sleep(.01)
            task=asyncio.create_task(consume())
            try:
                second=await router.answer(m.history('Confirm',first));self.assertIn('Step 2 of 2',second)
                self.assertEqual(self.bus.snapshot()['volume_percent'],20);self.assertEqual(router.actions.budget._used('music_control'),1)
                # A partly delivered next proposal cannot authorize step2.
                self.assertEqual(await router.answer(m.history('Confirm',second,'cancelled')),second)
                self.assertEqual(self.bus.snapshot()['volume_percent'],20)
                final=await router.answer(m.history('Confirmed',second));self.assertIn('checked',final)
                self.assertEqual(self.bus.snapshot()['volume_percent'],40);self.assertEqual(router.workflow.states,['verified','verified'])
                self.assertEqual(router.actions.budget._used('music_control'),2)
                self.assertIn('Playback started',final);self.assertIn('40 percent',final)
                self.assertIn('no pending',await router.answer(m.history('Confirm',final)));self.assertEqual(router.actions.budget._used('music_control'),2)
                self.assertEqual(transport.request.await_count,1)
            finally:task.cancel();await asyncio.gather(task,return_exceptions=True)

    async def test_read_schemas_advertised_once_and_every_nested_read_still_validates(self):
        async with Registry(self.events,self.cfg,music_bus=self.bus.path) as r:
            self.setup_actions(r,2);router=Router(m.Model(m.decision(text='Hello')),r,self.events)
            declarations=router.model_declarations(router.declarations())
            goal=next(d for d in declarations if d['name']==NAME)
            branch=next(b for b in goal['parameters']['properties']['steps']['items']['anyOf'] if 'music__get_playback_status' in b['properties']['tool']['enum'])
            self.assertEqual(branch['properties']['arguments'],{'type':'object'})
            self.assertIn('music__get_playback_status',{d['name'] for d in declarations})
            bad=[steps()[0],{'tool':'music__get_playback_status','arguments':{'unexpected':'private'}}]
            router.ordinary=m.Model(m.decision(NAME,{'steps':bad}))
            self.assertIn('safely',await router.answer(m.history('Play and read status')));self.assertEqual(r.calls,0)

    @unittest.skipUnless(shutil.which('ffmpeg'),'FFmpeg required for actual temporary decoder')
    async def test_mixed_goal_summary_keeps_groq_tool_identity_and_real_read_results(self):
        async with Registry(self.events,self.cfg,music_bus=self.bus.path) as r:
            self.setup_actions(r,1);plan=[steps()[0],{'tool':'music__get_playback_status','arguments':{}}]
            call={'id':'fixture_mixed','type':'function','function':{'name':NAME,'arguments':json.dumps({'steps':plan})}}
            transport=SimpleNamespace(request=AsyncMock(side_effect=[{'choices':[{'message':{'tool_calls':[call]}}]},{'choices':[{'message':{'content':'The music is playing at twenty percent.'}}]}]),close=AsyncMock())
            router=Router(GroqReasoning(self.events,transport=transport),r,self.events)
            first=await router.answer(m.history('Play and read status'))
            await router.answer(m.history('Confirm',first))
            items=transport.request.call_args.kwargs['json']['messages']
            synthetic=next(x for x in items if x.get('tool_calls'))['tool_calls'][0]
            response=next(x for x in items if x['role']=='tool')
            self.assertEqual(response['tool_call_id'],synthetic['id']);self.assertTrue(synthetic['id'].startswith('goal_'))
            report=json.loads(response['content'])['result'];self.assertEqual([s['status'] for s in report['steps']],['verified','read_received'])
            self.assertEqual(report['steps'][1]['result']['volume_percent'],20);self.assertEqual(transport.request.await_count,2)

class VerifiedOnlyGoalTests(unittest.IsolatedAsyncioTestCase):
    from tests.test_assistant_workflow import GoalTests as _Fixture
    asyncSetUp=_Fixture.asyncSetUp
    asyncTearDown=_Fixture.asyncTearDown
    cfg=_Fixture.cfg
    router=_Fixture.router
    rows=_Fixture.rows
    async def test_two_real_sqlite_effects_and_verified_replies_do_not_need_summary_service(self):
        from tests import test_assistant_mode as f
        async with f.Registry(self.events,self.cfg('shopping')) as r:
            selected=f.decision(NAME,{'steps':[{'tool':'propose_shopping_item','arguments':{'item':'notebooks','quantity':2}},{'tool':'propose_second_item','arguments':{'item':'pens','quantity':3}}]})
            router,model=self.router(r,selected,writes=2)
            first=await router.answer(f.history('Add notebooks then pens'))
            second=await router.answer(f.history('Confirm',first));self.assertIn('Step 2 of 2',second)
            final=await router.answer(f.history('Confirmed',second))
            self.assertIn('2 notebooks',final);self.assertIn('3 pens',final)
            self.assertEqual(self.rows(),[('notebooks',2),('pens',3)]);self.assertEqual(len(model.seen),1)
            self.assertIn('no pending',await router.answer(f.history('Confirm',final)));self.assertEqual(r.budget.used,2)

if __name__=='__main__':unittest.main()
