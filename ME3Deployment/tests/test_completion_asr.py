import asyncio,json,unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
from me3_completion.asr import StructuredGoogle,segment_config,parse_segments,SEGMENT_SCHEMA
from me3_core.pool import RequestPool
from me3_completion.server import completion_asr_server
class SchemaTests(unittest.TestCase):
    def test_schema_requires_both_bounded_string_fields_and_no_extras(self):
        config=segment_config('low');self.assertEqual(config.response_mime_type,'application/json');self.assertEqual(config.response_json_schema,SEGMENT_SCHEMA);self.assertEqual(set(SEGMENT_SCHEMA['required']),{'text','previous_text'});self.assertFalse(SEGMENT_SCHEMA['additionalProperties']);self.assertEqual(config.thinking_config.thinking_level,'LOW')
    def test_reject_bad_json_duplicates_missing_extra_wrong_type_and_long_text(self):
        for raw in ['no json','{"text":"a","text":"b","previous_text":"c"}','[]','{"text":"a"}',json.dumps({'text':'a','previous_text':'b','extra':'x'}),json.dumps({'text':False,'previous_text':'b'}),json.dumps({'text':'x'*1001,'previous_text':'b'}),None]:
            events=[]
            with self.subTest(raw_type=type(raw).__name__):
                with self.assertRaisesRegex(ValueError,'invalid_segment_transcription'):parse_segments(raw,lambda event,**kw:events.append((event,kw)))
                self.assertFalse(any('text' in kw or 'response' in kw for _,kw in events));self.assertEqual(len(events),1)
    def test_exact_empty_strings_and_unicode_are_valid_without_guessing(self):
        self.assertEqual(parse_segments('{"text":"","previous_text":"café"}',lambda *a,**k:None),{'text':'','previous_text':'café'})
    def test_completion_server_selects_new_provider_without_changing_public_server(self):
        with patch('me3_completion.server.asr_server',return_value='fixture') as build:
            self.assertEqual(completion_asr_server(),'fixture');pool=build.call_args.kwargs['pool'];self.assertIsInstance(pool,RequestPool);self.assertIs(pool.factory,StructuredGoogle)
class ProviderTests(unittest.IsolatedAsyncioTestCase):
    def provider(self,reply=None,error=None):
        p=StructuredGoogle.__new__(StructuredGoogle);self.events=[];p.emit=lambda event,**kw:self.events.append((event,kw));self.generate=AsyncMock(return_value=SimpleNamespace(text=reply,usage_metadata=None),side_effect=error);p.provider=SimpleNamespace(client=SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=self.generate))))
        return p
    async def test_single_clip_delegates_exact_accepted_implementation(self):
        p=self.provider()
        with patch('me3_completion.asr.ScopedGoogle.transcribe',new_callable=AsyncMock,return_value={'text':'hello'}) as original:
            self.assertEqual(await p.transcribe(b'pcm','low',None,'flac'),{'text':'hello'});original.assert_awaited_once_with(b'pcm','low',None,'flac')
        self.generate.assert_not_awaited()
    async def test_two_audio_clips_schema_one_attempt_and_no_payload_logging(self):
        p=self.provider('{"text":"of France?","previous_text":"What is the capital"}')
        with patch('me3_completion.asr.encode',new_callable=AsyncMock,side_effect=[(b'current','audio/flac'),(b'previous','audio/flac')]):result=await p.transcribe(b'currentpcm','low',b'previouspcm','flac')
        self.assertEqual(result['text'],'of France?');self.generate.assert_awaited_once();kw=self.generate.call_args.kwargs;self.assertEqual([x.inline_data.data for x in kw['contents'] if x.inline_data],[b'previous',b'current']);self.assertEqual(kw['config'].response_json_schema,SEGMENT_SCHEMA);self.assertFalse(any({'text','contents','url','headers','pcm','response'}&set(fields) for _,fields in self.events))
    async def test_error_and_cancellation_propagate_without_retry(self):
        for error in [ValueError('synthetic provider failure'),asyncio.CancelledError()]:
            p=self.provider(error=error)
            with patch('me3_completion.asr.encode',new_callable=AsyncMock,return_value=(b'flac','audio/flac')):
                with self.assertRaises(type(error)):await p.transcribe(b'pcm','low',b'old','flac')
            self.generate.assert_awaited_once()
    async def test_invalid_provider_result_stays_error_not_coerced_or_retried(self):
        p=self.provider('{"wrong":"data"}')
        with patch('me3_completion.asr.encode',new_callable=AsyncMock,return_value=(b'flac','audio/flac')):
            with self.assertRaisesRegex(ValueError,'invalid_segment_transcription'):await p.transcribe(b'pcm','low',b'old','flac')
        self.generate.assert_awaited_once();self.assertIn(('completion_asr_response_rejected',{'reason':'object_fields','response_retained':False}),self.events)
