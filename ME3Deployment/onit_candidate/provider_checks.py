"""Read-only capacity gates and local TLS diagnostics; no provider/account calls."""
import asyncio,copy,json,os,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,patch
import aiohttp
from onit_candidate import provider as p
from me3_assistant import groq_backend as g
from me3_assistant.admission import Admission
from tests.test_assistant_groq_adapter import Events
from onit_candidate.asr_connection_checks import ConnectionChecks

class MetadataChecks(unittest.TestCase):
    def test_input_and_output_limits_are_not_misclassified_as_generic_TPM(self):
        for message,expected in [('input tokens per minute (ITPM)','input_tokens_per_minute'),('output tokens per minute (OTPM)','output_tokens_per_minute'),('tokens per minute (TPM)','tokens_per_minute'),('ITPM and OTPM','multiple_named_limits')]:
            raw=json.dumps({'error':{'code':'rate_limit_exceeded','message':message}}).encode()
            self.assertEqual(p.rejection_metadata(raw)['limit_scope'],expected)
    def test_daily_tokens_are_distinguished_without_persisting_account_or_message(self):
        raw=json.dumps({'error':{'code':'rate_limit_exceeded','message':'PRIVATE_ORG: tokens per day (TPD). Limit 500000, Used 499900, Requested 3600. PRIVATE_PROMPT'}}).encode()
        result=p.rejection_metadata(raw)
        self.assertEqual((result['error_code'],result['limit_scope']),( 'rate_limit_exceeded','tokens_per_day'))
        self.assertEqual((result['limit'],result['used'],result['requested']),(500000,499900,3600));self.assertFalse(result['body_retained'])
        self.assertNotIn('PRIVATE_',str(result))
    def test_unknown_and_multiple_scopes_do_not_invent_a_single_reason(self):
        self.assertEqual(p.rejection_metadata(b'{"error":{"code":"quota_exceeded","message":"TPM and TPD"}}')['limit_scope'],'multiple_named_limits')
        self.assertEqual(p.rejection_metadata(b'{"error":{"code":"PRIVATE_CODE","message":"unclassified"}}')['error_code'],'unknown')
        for raw in (None,b'not JSON',b'[]',b'{"error":{"code":{}}}',b'x'*65537):self.assertEqual(p.rejection_metadata(raw)['limit_scope'],'unknown')

class SnapshotChecks(unittest.TestCase):
    def setup_state(self):
        a=Admission(clock=lambda:100);ticket=a.admit('reasoning',100)
        a.observe('reasoning',ticket,{'x-ratelimit-limit-requests':'1000','x-ratelimit-remaining-requests':'1000','x-ratelimit-limit-tokens':'8000','x-ratelimit-remaining-tokens':'8000','retry-after':'60'},429);a.finish('reasoning',ticket);return a
    def test_full_generic_headers_do_not_override_observed_429_cooldown(self):
        a=self.setup_state();before=copy.deepcopy(a.memory);result=p.capacity_snapshot(a,['reasoning'])
        self.assertFalse(result['ready']);self.assertEqual(result['reasons'][0]['reason'],'provider_cooldown');self.assertEqual(result['reasons'][0]['wait_seconds'],60)
        self.assertEqual(a.memory,before);self.assertFalse(result['provider_capacity_verified'])
    def test_expiry_returns_unknown_capacity_without_refilling_or_dispatching(self):
        a=self.setup_state();before=copy.deepcopy(a.memory);a.clock=lambda:161;result=p.capacity_snapshot(a,['reasoning'])
        self.assertTrue(result['ready']);self.assertFalse(result['provider_capacity_verified']);self.assertEqual(a.memory,before)
    def test_text_does_not_require_asr_capacity_but_voice_does(self):
        a=Admission(clock=lambda:100);ticket=a.admit('asr',0);a.observe('asr',ticket,{'retry-after':'60'},429);a.finish('asr',ticket)
        self.assertTrue(p.capacity_snapshot(a,['reasoning'])['ready']);self.assertFalse(p.capacity_snapshot(a,['reasoning','asr'])['ready'])
    def test_known_zero_balances_and_active_leases_block_without_new_attempt(self):
        for headers,expected in [({'x-ratelimit-limit-requests':'1000','x-ratelimit-remaining-requests':'0','x-ratelimit-reset-requests':'1h'},'known_request_balance'),({'x-ratelimit-limit-tokens':'8000','x-ratelimit-remaining-tokens':'0','x-ratelimit-reset-tokens':'30s'},'known_token_balance')]:
            a=Admission(clock=lambda:100);ticket=a.admit('reasoning',100);a.observe('reasoning',ticket,headers,200);a.finish('reasoning',ticket)
            self.assertEqual(p.capacity_snapshot(a,['reasoning'])['reasons'][0]['reason'],expected)
        a=Admission(clock=lambda:100);a.admit('reasoning',100);self.assertEqual(p.capacity_snapshot(a,['reasoning'])['reasons'][0]['reason'],'model_request_active')

class LocalTransportChecks(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=ConnectionChecks.asyncSetUp
    asyncTearDown=ConnectionChecks.asyncTearDown
    objects=ConnectionChecks.objects
    async def check(self,status):
        self.status=status;resolver,connector,events,unused=self.objects(legacy=True);t=p.Transport(events.emit,key='PRIVATE_KEY',opt_in=True,admission=Admission())
        try:
            with patch.object(p,'groq_connector',return_value=connector),patch.dict(g.ENDPOINTS,{'reasoning':'https://me3-fixture.invalid/'}),patch.dict(os.environ,{'NO_PROXY':'me3-fixture.invalid','no_proxy':'me3-fixture.invalid'}):
                if status==429:
                    with self.assertRaises(g.ProviderError) as caught:await t.request('reasoning',json={'max_completion_tokens':128})
                    self.assertEqual(caught.exception.code,429)
                    with self.assertRaises(ValueError):await t.request('reasoning',json={'max_completion_tokens':128})
                else:self.assertEqual((await t.request('reasoning',json={'max_completion_tokens':128}))['text'],'local fixture')
            self.assertEqual(self.posts,1);self.assertFalse(t.active);self.assertNotIn('PRIVATE_KEY',str(events.rows));return events.rows
        finally:await t.close();await unused.close()
    async def test_429_retains_original_error_and_blocks_another_post(self):
        rows=await self.check(429);observed=[v for n,v in rows if n=='onit_provider_capacity_rejection']
        self.assertEqual(len(observed),1);self.assertEqual(observed[0]['status'],429);self.assertFalse(observed[0]['body_retained']);self.assertFalse(observed[0]['automatic_retry'])
    async def test_200_content_and_single_request_are_unchanged(self):
        rows=await self.check(200);self.assertFalse(any(n=='onit_provider_capacity_rejection' for n,_ in rows))

class TraceChecks(unittest.IsolatedAsyncioTestCase):
    async def test_oversized_or_unreadable_body_keeps_safe_unknown_metadata(self):
        for data in (b'x'*70000,None):
            events=Events();trace=p.capacity_trace(events.emit);trace.freeze()
            if data is None:read=AsyncMock(side_effect=OSError('PRIVATE_MESSAGE'))
            else:
                chunks=[data[i:i+16384] for i in range(0,len(data),16384)]+[b''];read=AsyncMock(side_effect=chunks)
            response=NS(status=429,headers={},content=NS(read=read))
            await trace.on_request_end.send(None,NS(),NS(response=response))
            self.assertEqual(events.rows[0][1]['limit_scope'],'unknown');self.assertNotIn('PRIVATE_MESSAGE',str(events.rows))
    async def test_canceling_diagnostics_never_turns_into_a_retry_or_success(self):
        entered=asyncio.Event()
        async def hold(n):entered.set();await asyncio.Event().wait()
        events=Events();trace=p.capacity_trace(events.emit);trace.freeze();response=NS(status=429,headers={},content=NS(read=hold))
        task=asyncio.create_task(trace.on_request_end.send(None,NS(),NS(response=response)));await entered.wait();task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(events.rows,[])

class GUIChecks(unittest.IsolatedAsyncioTestCase):
    async def test_known_capacity_block_precedes_voice_start_and_preserves_console_owner(self):
        from onit_candidate.gui import Console
        from me3_assistant.gui import Console as BaseConsole
        with patch('onit_candidate.gui.verify_candidate',return_value={}):c=Console({},dict(revision='fixture'))
        with patch('onit_candidate.gui.verify_candidate',return_value={}),patch.object(BaseConsole,'gate',return_value=({},{})),patch.object(p,'require_capacity',side_effect=ValueError('service capacity unavailable')) as check:
            with self.assertRaisesRegex(ValueError,'capacity'):c.gate('normal')
        check.assert_called_once();self.assertIsNone(c.task)
    async def test_provider_capacity_failure_is_reported_as_capacity_after_shutdown(self):
        from onit_candidate.gui import Console
        with patch('onit_candidate.gui.verify_candidate',return_value={}):c=Console({},dict(revision='fixture'))
        c.stop_event=asyncio.Event()
        async def failed(*a,**kw):c.observe('onit_provider_capacity_rejection',status=429,limit_scope='unknown');raise g.ProviderError(429)
        with patch('onit_candidate.voice.run',side_effect=failed):await c.voice()
        self.assertIn('capacity',c.public_error);self.assertEqual(c.state,'stopped');self.assertFalse(c.voice_armed)

if __name__=='__main__':unittest.main(verbosity=2)
