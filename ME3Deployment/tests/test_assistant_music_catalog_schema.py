"""Actual discovered IDs constrain plans; preparation failures retain safe codes."""
import json,unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
from me3_assistant.registry import Registry
from me3_assistant.router import Router
from me3_assistant.workflow import NAME
from me3_assistant.groq_backend import GroqReasoning
from tests.test_assistant_music import MusicMCPTests,Model,decision,history

def enums(schema):
    if isinstance(schema,dict):
        return set(schema.get('enum',[]))|set().union(*(enums(v) for v in schema.values()))
    if isinstance(schema,list):return set().union(*(enums(v) for v in schema))
    return set()

class MusicCatalogSchemaTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=MusicMCPTests.asyncSetUp
    asyncTearDown=MusicMCPTests.asyncTearDown
    setup_actions=MusicMCPTests.setup_actions

    async def test_discovered_action_exposes_real_track_id_and_keeps_control_fields_private(self):
        async with Registry(self.events,self.cfg,music_bus=self.bus.path) as r:
            a=self.setup_actions(r,2);d=a.declarations()[0];props=d['parameters']['properties']
            self.assertEqual(enums(props['track_id']),{'Study.wav'})
            self.assertFalse({'token','operation_id','confirmation','generation','request_id'} & set(props))
            self.assertEqual(r.calls,0);self.assertEqual(self.bus.snapshot()['state'],'stopped')

    async def test_spoken_filename_is_not_an_authorized_id_and_blocks_entire_plan(self):
        async with Registry(self.events,self.cfg,music_bus=self.bus.path) as r:
            self.setup_actions(r,2)
            selected=decision(NAME,{'steps':[{'tool':'propose_music_control','arguments':{'command':'play','track_id':'Study dot wav','volume':20}},{'tool':'propose_music_control','arguments':{'command':'volume','volume':40}}]})
            router=Router(Model(selected),r,self.events)
            reply=await router.answer(history('Play Study dot wav then set40percent'))
            self.assertIn('safely',reply);self.assertEqual(r.calls,0);self.assertEqual(router.actions.budget._used('music_control'),0);self.assertIsNone(router.pending)
            self.assertEqual(self.bus.snapshot()['state'],'stopped')

    async def test_real_identifiers_prepare_first_step_without_execution_or_losing_later_step(self):
        async with Registry(self.events,self.cfg,music_bus=self.bus.path) as r:
            self.setup_actions(r,2)
            selected=decision(NAME,{'steps':[{'tool':'propose_music_control','arguments':{'command':'play','track_id':'Study.wav','volume':20}},{'tool':'propose_music_control','arguments':{'command':'volume','volume':40}}]})
            router=Router(Model(selected),r,self.events)
            reply=await router.answer(history('Play Study dot wav then set40percent'))
            self.assertIn('Step 1 of 2',reply);self.assertIn('Study.wav',reply);self.assertEqual(len(router.workflow.steps),2)
            self.assertEqual(router.workflow.states,['needs_confirmation','unstarted']);self.assertEqual(r.calls,1);self.assertEqual(router.actions.budget._used('music_control'),0)
            self.assertEqual(self.bus.snapshot()['state'],'stopped')

    async def test_argument_failure_records_only_safe_code_before_any_rpc(self):
        async with Registry(self.events,self.cfg,music_bus=self.bus.path) as r:
            a=self.setup_actions(r,2)
            reply=await a.propose('propose_music_control',{'command':'play','track_id':'PRIVATE_UNTRUSTED_NO_EXTENSION','volume':20},1)
            self.assertIn('No execution',reply);self.assertEqual(r.calls,0);self.assertIsNone(a.pending)
            events=[e for e in self.events.rows if e['event']=='assistant_music_arguments_rejected']
            self.assertEqual(len(events),1);self.assertEqual(events[0]['validation_reason'],'invalid_track_id');self.assertFalse(events[0]['execution_started']);self.assertNotIn('PRIVATE_UNTRUSTED',json.dumps(events))
            with patch('me3_assistant.music_action.intent',side_effect=ValueError('PRIVATE_EXCEPTION_VALUE')):
                await a.propose('propose_music_control',{'command':'play','track_id':'Study.wav'},1)
            last=[e for e in self.events.rows if e['event']=='assistant_music_arguments_rejected'][-1]
            self.assertEqual(last['validation_reason'],'invalid_music_arguments');self.assertNotIn('PRIVATE_EXCEPTION_VALUE',json.dumps(last))

    async def test_actual_discovered_enum_is_forwarded_to_groq_without_builtin_execution(self):
        async with Registry(self.events,self.cfg,music_bus=self.bus.path) as r:
            a=self.setup_actions(r,2)
            transport=SimpleNamespace(request=AsyncMock(return_value={'choices':[{'message':{'content':'Which track?'}}]}),close=AsyncMock())
            m=GroqReasoning(self.events,transport=transport)
            await m.decide(history('Play Study dot wav then set40percent'),a.declarations(),[])
            payload=transport.request.call_args.kwargs['json'];schema=payload['tools'][0]['function']['parameters']
            self.assertEqual(enums(schema['properties']['track_id']),{'Study.wav'});self.assertFalse(payload['parallel_tool_calls'])
            self.assertEqual(r.calls,0);self.assertEqual(self.bus.snapshot()['state'],'stopped');await m.close()

if __name__=='__main__':unittest.main()
