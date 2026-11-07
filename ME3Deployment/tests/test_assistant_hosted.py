"""No provider construction: exercise the real decision config and cleanup."""
import asyncio,unittest
from types import SimpleNamespace as NS
from unittest.mock import patch,AsyncMock
from google.genai import types
from me3_assistant.reasoning import Reasoning
from me3_core.pool import SCOPE

class Events:
    current_generation=7
    def __init__(self):self.rows=[]
    def emit(self,event,**kw):self.rows.append({'event':event,**kw})

def initialize(self,events):
    self.events=events;self.lock=asyncio.Lock()
    self.generate=AsyncMock(return_value=types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(role='model',parts=[types.Part(text='Canada.')]))],usage_metadata=types.GenerateContentResponseUsageMetadata(candidates_token_count=2,thoughts_token_count=0)))
    self.traced=NS(provider=NS(client=NS(aio=NS(models=NS(generate_content=self.generate)))))

class HostedPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.patches=[patch('me3_core.reasoning.CoreReasoning.__init__',initialize),patch('me3_assistant.reasoning.bind_connection_deadline'),patch('me3_assistant.reasoning.connection_timeouts',return_value=0)]
        for p in self.patches:p.start()
    async def asyncTearDown(self):
        for p in reversed(self.patches):p.stop()
    async def call(self,model):
        return await model.decide([{'type':'user_input','content':[{'text':'Which country?'}]}],[{'name':'check_status','description':'Read status','parameters':{'type':'object','properties':{}}}],[])
    async def test_default_low_and_explicit_minimal_preserve_tools_and_retry_boundary(self):
        for profile in ('low','minimal'):
            e=Events();m=Reasoning(e,**({'thinking_profile':profile} if profile=='minimal' else {}))
            _,calls,text=await self.call(m);c=m.generate.call_args.kwargs['config']
            self.assertEqual(c.thinking_config.thinking_level.value,profile.upper())
            self.assertEqual(c.max_output_tokens,1024);self.assertTrue(c.automatic_function_calling.disable)
            self.assertEqual(c.tools[0].function_declarations[0].name,'check_status')
            self.assertEqual(text,'Canada.');self.assertEqual(calls,[]);self.assertEqual(m.generate.await_count,1)
            self.assertFalse(m.lock.locked());self.assertIsNone(SCOPE.get())
            self.assertEqual(e.rows[-1]['thought_tokens'],0)
            self.assertNotIn('Canada',str(e.rows))
    async def test_invalid_profile_rejected_before_client_construction(self):
        for value in ('high','LOW','',None):
            with self.subTest(value=value),self.assertRaises(ValueError):Reasoning(Events(),thinking_profile=value)
    async def test_error_not_retried_and_context_released(self):
        m=Reasoning(Events());m.generate.side_effect=ValueError('fixture_failure')
        with self.assertRaises(ValueError):await self.call(m)
        self.assertEqual(m.generate.await_count,1);self.assertFalse(m.lock.locked());self.assertIsNone(SCOPE.get())
    async def test_cancellation_releases_lock_and_never_returns_stale_text(self):
        m=Reasoning(Events());entered=asyncio.Event()
        async def stalled(**kw):entered.set();await asyncio.Event().wait()
        m.generate.side_effect=stalled;t=asyncio.create_task(self.call(m));await entered.wait();t.cancel()
        with self.assertRaises(asyncio.CancelledError):await t
        self.assertFalse(m.lock.locked());self.assertIsNone(SCOPE.get());self.assertEqual(m.generate.await_count,1)
    async def test_missing_usage_and_empty_candidate_are_safe(self):
        m=Reasoning(Events());m.generate.return_value=types.GenerateContentResponse(candidates=[])
        with self.assertRaisesRegex(ValueError,'empty_assistant_decision'):await self.call(m)
        self.assertIsNone(m.events.rows[-1]['output_tokens']);self.assertFalse(m.lock.locked())
