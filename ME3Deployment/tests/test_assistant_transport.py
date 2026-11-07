import asyncio,ssl,unittest
from types import SimpleNamespace
from unittest.mock import patch
from me3_assistant.transport import bind_connection_deadline,connection_timeouts,BoundedScopedGoogle
from me3_core.pool import ScopedGoogle

class Events:
    def __init__(self):self.rows=[]
    def emit(self,event,**fields):self.rows.append((event,fields))

class TransportTests(unittest.IsolatedAsyncioTestCase):
    def provider(self,original):
        events=Events();connector=SimpleNamespace(_create_connection=original)
        return SimpleNamespace(owned_session=SimpleNamespace(connector=connector),emit=events.emit),events
    async def test_fast_connection_result_and_arguments_unchanged(self):
        sentinel=object();calls=[]
        async def original(*args,**kw):calls.append((args,kw));return sentinel
        provider,events=self.provider(original);bind_connection_deadline(provider)
        self.assertIs(await provider.owned_session.connector._create_connection('request',ssl=True),sentinel)
        self.assertEqual(calls,[(('request',),{'ssl':True})]);self.assertEqual(events.rows,[]);self.assertEqual(connection_timeouts(provider),0)
    async def test_stalled_creation_cancelled_once_without_retry(self):
        entered=0;released=asyncio.Event()
        async def original():
            nonlocal entered;entered+=1
            try:await asyncio.Future()
            finally:released.set()
        provider,events=self.provider(original);bind_connection_deadline(provider,.02)
        with self.assertRaises(TimeoutError):await provider.owned_session.connector._create_connection()
        self.assertTrue(released.is_set());self.assertEqual(entered,1);self.assertEqual(connection_timeouts(provider),1)
        self.assertEqual(events.rows[0][0],'normal_connection_deadline');self.assertFalse(events.rows[0][1]['automatic_retry'])
    async def test_caller_cancel_not_reclassified_as_connection_timeout(self):
        entered=asyncio.Event();released=asyncio.Event()
        async def original():
            entered.set()
            try:await asyncio.Future()
            finally:released.set()
        provider,events=self.provider(original);bind_connection_deadline(provider)
        task=asyncio.create_task(provider.owned_session.connector._create_connection());await entered.wait();task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertTrue(released.is_set());self.assertEqual(connection_timeouts(provider),0);self.assertEqual(events.rows[0][0],'normal_connection_creation_cancelled')
    async def test_original_timeout_not_counted_as_our_deadline(self):
        async def original():raise TimeoutError('fixture')
        provider,events=self.provider(original);bind_connection_deadline(provider)
        with self.assertRaises(TimeoutError):await provider.owned_session.connector._create_connection()
        self.assertEqual(connection_timeouts(provider),0);self.assertEqual(events.rows,[])
    async def test_actual_tls_handshake_stall_cleanly_bounded_with_verification_enabled(self):
        import aiohttp
        accepted=asyncio.Event();release=asyncio.Event();handlers=set()
        async def peer(reader,writer):
            task=asyncio.current_task();handlers.add(task);accepted.set()
            try:await release.wait()
            finally:writer.close();await writer.wait_closed();handlers.discard(task)
        server=await asyncio.start_server(peer,'127.0.0.1',0);port=server.sockets[0].getsockname()[1];events=Events();connector=aiohttp.TCPConnector()
        self.assertTrue(connector._ssl);original_ssl=connector._ssl
        try:
            async with aiohttp.ClientSession(connector=connector) as session:
                # Exercise production's3s total/1s address policy. The former
                # accelerated100ms/33ms fixture could expire before local TCP
                # acceptance on Windows and never exercise a stalled handshake.
                provider=SimpleNamespace(owned_session=session,emit=events.emit);bind_connection_deadline(provider)
                self.assertEqual(connector._me3_normal_connect_seconds,3)
                with self.assertRaises(TimeoutError):await session.get('https://127.0.0.1:'+str(port)+'/')
                self.assertTrue(accepted.is_set());self.assertEqual(connection_timeouts(provider),1);self.assertIs(connector._ssl,original_ssl)
                self.assertFalse(connector._acquired)
                self.assertTrue(any(name=='normal_address_connect_start' and fields['address_seconds']==1 for name,fields in events.rows))
        finally:
            release.set();server.close();await server.wait_closed()
            if handlers:await asyncio.gather(*list(handlers))
        self.assertTrue(connector.closed)
    async def test_wrapped_sdk_error_standardized_only_after_our_deadline(self):
        provider=BoundedScopedGoogle.__new__(BoundedScopedGoogle);connector=SimpleNamespace(_me3_normal_connect_timeouts=0);provider.owned_session=SimpleNamespace(connector=connector)
        async def failure(*args,**kwargs):connector._me3_normal_connect_timeouts+=1;raise ValueError('wrapped fixture')
        with patch.object(ScopedGoogle,'transcribe',failure):
            with self.assertRaises(TimeoutError):await provider.transcribe(b'fixture','low')
        async def auth_failure(*args,**kwargs):raise PermissionError('fixture401')
        with patch.object(ScopedGoogle,'transcribe',auth_failure):
            with self.assertRaises(PermissionError):await provider.transcribe(b'fixture','low')
    async def test_idempotent_binding_and_policy_validation(self):
        async def original():return 1
        provider,events=self.provider(original);bind_connection_deadline(provider,3);bound=provider.owned_session.connector._create_connection
        bind_connection_deadline(provider,3);self.assertIs(provider.owned_session.connector._create_connection,bound)
        with self.assertRaises(ValueError):bind_connection_deadline(provider,2)
        for invalid in (0,-1,11,True,float('nan')):
            p,_=self.provider(original)
            with self.assertRaises(ValueError):bind_connection_deadline(p,invalid)
        with self.assertRaisesRegex(ValueError,'connector_required'):bind_connection_deadline(SimpleNamespace())
    async def test_normal_mcp_keeps_actual_asr_tool_schemas(self):
        from me3_assistant.server import make_asr_server
        server=make_asr_server();tools=await server.list_tools();self.assertEqual({t.name for t in tools},{'server_info','request_status','transcribe'})
        transcribe=next(t for t in tools if t.name=='transcribe');self.assertIn('pcm_b64',transcribe.inputSchema['properties']);self.assertIn('request_id',transcribe.inputSchema['properties'])

if __name__=='__main__':unittest.main()
