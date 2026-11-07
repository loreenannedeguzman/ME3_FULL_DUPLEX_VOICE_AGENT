import asyncio,json,unittest
from types import SimpleNamespace
from unittest.mock import patch
from me3_m7.provider import TracedGoogle
class Session:
    async def request(self,*args,**kwargs):return SimpleNamespace(status=200)
class API:
    def __init__(self):self.session=Session()
    def _use_aiohttp(self):return True
    async def _get_aiohttp_session(self):return self.session
    async def _async_request_once(self):return await (await self._get_aiohttp_session()).request()
class ProviderTraceTests(unittest.IsolatedAsyncioTestCase):
    def setup_provider(self,reply='four',double=False):
        self.events=[];self.calls=[];api=API();outer=self
        class Fake:
            def __init__(self):
                async def generate(**kw):
                    outer.calls.append(kw);await api._async_request_once()
                    if double:await api._async_request_once()
                    return SimpleNamespace(text=reply,usage_metadata=SimpleNamespace(thoughts_token_count=5,candidates_token_count=2))
                self.client=SimpleNamespace(_api_client=api,aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate)))
            async def close(self):pass
        with patch('me3_m7.provider.GoogleSpeech',Fake):return TracedGoogle(lambda event,**kw:self.events.append({'event':event,**kw}))
    async def test_profile_and_payload_keep_original_single_clip_prompt(self):
        p=self.setup_provider();result=await p.transcribe(bytes(6400),'low');self.assertEqual(result['text'],'four');self.assertEqual(self.calls[0]['config'].thinking_config.thinking_level,'LOW');self.assertEqual(self.calls[0]['contents'][1].inline_data.mime_type,'audio/wav');self.assertEqual(p.wire,1);await p.close()
    async def test_minimal_is_explicit_and_trace_has_no_payload(self):
        p=self.setup_provider();await p.transcribe(bytes(6400),'minimal');self.assertEqual(self.calls[0]['config'].thinking_config.thinking_level,'MINIMAL');self.assertTrue(any(x['event']=='http_response_headers' for x in self.events));self.assertTrue(all(not {'text','contents','headers','url','pcm'}&x.keys() for x in self.events));await p.close()
    async def test_second_wire_dispatch_blocked_before_network(self):
        p=self.setup_provider(double=True)
        with self.assertRaisesRegex(ValueError,'hidden_retry'):await p.transcribe(bytes(6400),'low')
        self.assertEqual(p.wire,1);self.assertEqual(sum(x['event']=='http_request_start' for x in self.events),1);await p.close()
    async def test_two_clips_remain_separate_and_schema_is_strict(self):
        p=self.setup_provider(reply=json.dumps({'previous_text':'What is the capital','text':'of France?'}));data=await p.transcribe(bytes(6400),'minimal',bytes(3200));parts=self.calls[0]['contents'];audio=[x.inline_data.data for x in parts if x.inline_data];self.assertEqual(len(audio),2);self.assertEqual([len(x) for x in audio],[3244,6444]);self.assertEqual(data['text'],'of France?');await p.close()
