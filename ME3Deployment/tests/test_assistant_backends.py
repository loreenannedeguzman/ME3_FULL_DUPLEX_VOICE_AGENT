"""Provider-free normal/GUI selection and real MCP tool-result regressions."""
import asyncio,json,os,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch,AsyncMock
from me3_assistant import backends as B
from me3_assistant.gui import Console,row
from me3_assistant.guard import authorize
from tests import test_assistant_mode as fixtures

class BackendTests(unittest.TestCase):
    def test_google_is_default_even_if_groq_key_environment_present(self):
        with patch.dict(os.environ,{'GROQ_API_KEY':'fixture'}):
            self.assertEqual(B.selected({}),'google')
            self.assertEqual(B.speech_module('google'),'me3_assistant.server')
            self.assertEqual(B.reasoning_class('google').__name__,'Reasoning')
    def test_unknown_and_mismatched_provider_rejected_without_fallback(self):
        for grant,request in (({},'groq'),({'backend':'groq'},'google'),({'backend':'other'},None)):
            with self.assertRaises(ValueError):B.selected(grant,request)
        with self.assertRaises(ValueError):B.reasoning_class('other')
    def test_selected_groq_has_explicit_separate_asr_module(self):
        self.assertEqual(B.selected({'backend':'groq'},'groq'),'groq')
        self.assertEqual(B.speech_module('groq'),'me3_assistant.groq_server')
        self.assertEqual(B.reasoning_class('groq').__name__,'JSONGroqReasoning')
    def test_groq_audio_configuration_does_not_require_google_credentials(self):
        with patch.dict(os.environ,{},clear=True),patch.object(B,'groq_key',return_value='fixture'):
            settings=B.audio_settings('groq');self.assertEqual(settings.api_key,'')
        with patch.dict(os.environ,{},clear=True),self.assertRaises(ValueError):B.audio_settings('google')
    def test_key_is_private_file_not_environment_and_never_interpolated(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'key';p.write_text('GROQ_API_KEY=fixture-${OTHER}\n');p.chmod(0o600)
            with patch.dict(os.environ,{'GROQ_API_KEY':'wrong','OTHER':'private'}):self.assertEqual(B.groq_key(p),'fixture-${OTHER}')
            p.write_text('GROQ_API_KEY="bad key"\n')
            with self.assertRaises(ValueError):B.groq_key(p)
    @unittest.skipIf(os.name=='nt','POSIX permission enforcement')
    def test_insecure_key_file_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'key';p.write_text('GROQ_API_KEY=fixture\n');p.chmod(0o644)
            with self.assertRaises(ValueError):B.groq_key(p)
    def test_grant_provider_is_enforced_with_existing_source_budget_binding(self):
        with tempfile.TemporaryDirectory() as d:
            fixture=NS(folder=Path(d));cfg,src,g,now=fixtures.ConfigTests.grant(fixture);p=Path(d)/'grant.json'
            g['backend']='groq';p.write_text(json.dumps(g));got,_=authorize(cfg,src,p,Path(d),now,backend='groq');self.assertEqual(got['backend'],'groq')
            with self.assertRaisesRegex(ValueError,'backend_authorization'):authorize(cfg,src,p,Path(d),now,backend='google')

class GUIBackendTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_groq_credential_rejected_before_session_task_or_audio(self):
        c=Console({'sha256':'fixture'},{'revision':'fixture'})
        with patch.object(c,'gate',return_value=({'backend':'groq'},{})),patch.object(B,'groq_key',side_effect=ValueError('private_groq_key_file_required')):
            with self.assertRaises(ValueError):await c.command({'action':'start_voice','backend':'groq'})
        self.assertIsNone(c.task);self.assertEqual(c.state,'stopped')
    async def test_gui_passes_requested_provider_to_gate_and_freezes_while_active(self):
        c=Console({'sha256':'fixture'},{'revision':'fixture'})
        async def waiting():await asyncio.Event().wait()
        with patch.object(c,'gate',return_value=({'backend':'groq'},{})) as gate,patch.object(B,'check_ready'),patch.object(c,'voice',waiting):
            await c.command({'action':'start_voice','backend':'groq'});gate.assert_called_once_with('normal',backend='groq');self.assertEqual(c.backend,'groq')
            with self.assertRaises(ValueError):await c.command({'action':'start_text','backend':'google'})
            await c.stop();self.assertEqual(c.state,'stopped')
    async def test_real_fixture_mcp_round_trip_with_groq_function_ids_and_no_provider(self):
        from me3_assistant.groq_backend import GroqReasoning
        from me3_assistant.registry import Registry
        from me3_assistant.router import Router
        cfg=fixtures.config('extension')
        first={'choices':[{'message':{'content':None,'tool_calls':[{'id':'fixture_1','type':'function','function':{'name':'configured__count_words','arguments':'{"text":"one two"}'}}]}}]}
        last={'choices':[{'message':{'content':'Two words.'}}]}
        transport=NS(request=AsyncMock(side_effect=[first,last]),close=AsyncMock())
        with tempfile.TemporaryDirectory() as d:
            events=fixtures.Events(Path(d)/'events.jsonl');model=GroqReasoning(events,transport=transport)
            async with Registry(events,cfg) as registry:
                from me3_assistant.actions import Journal
                registry.allowed_actions=[];registry.budget=fixtures.FixtureBudget();registry.journal=Journal(Path(d)/'actions.jsonl');router=Router(model,registry,events)
                router.controller=NS(current=lambda g:True,clock=NS(current=1))
                self.assertEqual(await router.answer([row('user','Count the words one two')]),'Two words.')
                self.assertEqual(registry.calls,1)
                payload=transport.request.call_args.kwargs['json'];self.assertEqual(payload['messages'][-1]['tool_call_id'],'fixture_1')
            await model.close();transport.close.assert_awaited_once()

if __name__=='__main__':unittest.main()
