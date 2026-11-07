import asyncio,unittest
from types import SimpleNamespace
from unittest.mock import patch
from aiohttp import web,ClientSession
from me3_core.pool import RequestPool,ScopedGoogle
from me3_m7.server import ASRService
import base64
B64=base64.b64encode(bytes(6400)).decode()
class HTTPReuseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests=0;self.connections=set();self.block=False;self.double=False;self.entered=asyncio.Event();self.gate=asyncio.Event()
        async def handle(request):
            self.requests+=1;self.connections.add(request.transport);self.entered.set()
            if self.block:await self.gate.wait()
            return web.json_response({'ok':True})
        app=web.Application();app.router.add_post('/',handle);self.runner=web.AppRunner(app);await self.runner.setup();site=web.TCPSite(self.runner,'127.0.0.1',0);await site.start();self.url='http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1])+'/'
        outer=self
        class API:
            def __init__(inner):inner.session=None;inner._http_options=SimpleNamespace(aiohttp_client=None)
            def _use_aiohttp(inner):return True
            async def _get_aiohttp_session(inner):
                if inner._http_options.aiohttp_client is not None:return inner._http_options.aiohttp_client
                if inner.session is None:inner.session=ClientSession()
                return inner.session
            async def _async_request_once(inner):
                response=await (await inner._get_aiohttp_session()).request('POST',outer.url,json={'synthetic':True});await response.read();return response
        class FakeGoogle:
            def __init__(inner):
                inner.api=API()
                async def generate(**_):
                    await inner.api._async_request_once()
                    return SimpleNamespace(text='fixture',usage_metadata=SimpleNamespace(thoughts_token_count=0,candidates_token_count=1))
                inner.client=SimpleNamespace(_api_client=inner.api,aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate)))
            async def answer(inner,history):
                await inner.api._async_request_once()
                if outer.double:await inner.api._async_request_once()
                return 'fixture reply'
            async def close(inner):
                if inner.api.session is not None:await inner.api.session.close()
        self.patcher=patch('me3_m7.provider.GoogleSpeech',FakeGoogle);self.patcher.start();self.rows=[];self.pool=RequestPool();self.service=ASRService(self.pool.lease,lambda event,**kw:self.rows.append({'event':event,**kw}))
    async def asyncTearDown(self):
        self.gate.set();await self.pool.shutdown();self.patcher.stop();await self.runner.cleanup()
    async def test_real_local_http_keepalive_and_request_identity(self):
        for g in (1,2):self.assertTrue((await self.service.call(B64,g,'local-'+str(g)))['ok'])
        self.assertEqual(self.requests,2);self.assertEqual(len(self.connections),1)
        owned=self.pool.cached.owned_session;self.assertFalse(owned.closed);self.assertEqual(owned.connector.limit,1);await self.pool.shutdown();self.assertTrue(owned.closed)
        reuse=[r for r in self.rows if r['event']=='core_transport_connection_reused'];self.assertEqual([r['request_id'] for r in reuse],['local-2'])
        wire=[r for r in self.rows if r['event']=='http_request_start'];self.assertEqual([r['wire_attempt'] for r in wire],[1,1]);self.assertEqual([r['request_id'] for r in wire],['local-1','local-2'])
    async def test_canceled_http_is_retired_and_next_uses_new_connection(self):
        self.block=True;task=asyncio.create_task(self.service.call(B64,1,'cancel-http'));await self.entered.wait();task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertTrue(self.service.states['cancel-http']['cleanup_ok']);self.assertIsNone(self.pool.cached)
        self.block=False;self.gate.set();self.assertTrue((await self.service.call(B64,2,'next-http'))['ok']);self.assertEqual(len(self.connections),2)
        canceled=[r for r in self.rows if r['event']=='http_cancel_observed'];self.assertEqual([r['request_id'] for r in canceled],['cancel-http'])

    async def test_text_calls_keep_separate_wire_budgets_and_generation_labels(self):
        from me3_core.reasoning import CoreReasoning
        rows=[];events=SimpleNamespace(current_generation=1,emit=lambda event,**kw:rows.append({'event':event,**kw}));reasoner=CoreReasoning(events)
        try:
            await reasoner.answer([]);events.current_generation=2;await reasoner.answer([])
            wire=[r for r in rows if r['event']=='core_text_http_request_start'];self.assertEqual([r['generation'] for r in wire],[1,2]);self.assertEqual([r['wire_attempt'] for r in wire],[1,1]);self.assertEqual(self.requests,2)
        finally:await reasoner.close()
    async def test_text_hidden_retry_is_blocked_before_second_wire_request(self):
        from me3_core.reasoning import CoreReasoning
        self.double=True;events=SimpleNamespace(current_generation=1,emit=lambda *a,**k:None);reasoner=CoreReasoning(events)
        try:
            with self.assertRaisesRegex(ValueError,'hidden_retry'):await reasoner.answer([])
            self.assertEqual(self.requests,1)
        finally:await reasoner.close()

class PinnedSDKOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_sdk_construction_closes_custom_session_without_provider_request(self):
        import os
        from me3_core.pool import ScopedGoogle,RequestScope,SCOPE,dispatch
        rows=[];token=SCOPE.set(RequestScope(lambda event,**kw:rows.append(event)))
        try:
            with patch.dict(os.environ,{'ALLOW_PROVIDER_AUDIO':'1','GOOGLE_API_KEY':'fixture-not-a-real-key'}):provider=ScopedGoogle(dispatch)
            owned=provider.owned_session;self.assertIs(provider.provider.client._api_client._http_options.aiohttp_client,owned)
            self.assertNotIn('http_request_start',rows);await provider.close();self.assertTrue(owned.closed)
        finally:SCOPE.reset(token)
