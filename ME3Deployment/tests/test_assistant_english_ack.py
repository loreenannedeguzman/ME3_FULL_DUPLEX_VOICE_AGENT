import json,unittest
from me3_assistant.groq_backend import GroqASR,ProviderError,error_metadata
from me3_assistant.groq_json import response_schema
from tests import test_assistant_groq_adapter as a
from tests import test_assistant_mode as f
from tests import test_assistant_recovery as recovery

class LanguageTests(unittest.IsolatedAsyncioTestCase):
 async def test_English_default_is_sent_once_with_original_audio_not_translated_or_retried(self):
  t=a.fake_transport({'text':'A new user question.','language':'english'});m=GroqASR(lambda *a,**k:None,transport=t)
  self.assertEqual((await m.transcribe(bytes(32000),'low'))['text'],'A new user question.')
  fields=t.request.call_args.kwargs['data']._fields;values={header['name']:value for header,_,value in fields}
  self.assertEqual(values['language'],'en');self.assertEqual(values['model'],'whisper-large-v3-turbo');self.assertEqual(t.request.await_count,1)
  self.assertTrue(values['file'].startswith(b'RIFF'));self.assertEqual(len(values['file']),32044)
 async def test_conflicting_reported_language_cannot_reach_reasoning_as_valid_transcript(self):
  t=a.fake_transport({'text':'private wrong-language words','language':'indonesian'});events=a.Events();m=GroqASR(events.emit,transport=t)
  with self.assertRaisesRegex(ValueError,'asr_language_mismatch'):await m.transcribe(bytes(32000),'low')
  self.assertEqual(t.request.await_count,1);self.assertNotIn('private',str(events.rows))
 async def test_other_explicit_language_and_deliberate_auto_mode_remain_available(self):
  for language in ('fr',None):
   t=a.fake_transport({'text':'Une question.','language':'french'});m=GroqASR(lambda *a,**k:None,transport=t,language=language)
   await m.transcribe(bytes(32000),'low');values={h['name']:v for h,_,v in t.request.call_args.kwargs['data']._fields}
   if language is None:self.assertNotIn('language',values)
   else:self.assertEqual(values['language'],'fr')
  for language in ('',False,'english','en\n','EN'):
   with self.subTest(language=language),self.assertRaises(ValueError):GroqASR(lambda *a,**k:None,transport=a.fake_transport({}),language=language)

class AcknowledgmentTests(unittest.IsolatedAsyncioTestCase):
 asyncSetUp=f.MCPTests.asyncSetUp
 asyncTearDown=f.MCPTests.asyncTearDown
 cfg=f.MCPTests.cfg
 router=recovery.RecoveryTests.router
 async def test_social_acknowledgments_make_no_hosted_request_or_rpc(self):
  async with f.Registry(self.events,self.cfg()) as r:
   router,model=self.router(r)
   for text in ("Don't worry.",'No worries!','Thank you.','Got it.'):
    self.assertEqual(await router.answer(f.history(text)),'Okay.')
   self.assertEqual(model.seen,[]);self.assertEqual(r.calls,0);self.assertEqual(r.journal.records,{})
 async def test_ack_containing_new_request_is_not_swallowed(self):
  async with f.Registry(self.events,self.cfg()) as r:
   router,model=self.router(r,f.decision(text='A new explanation.'))
   self.assertEqual(await router.answer(f.history("Don't worry about that; explain the next topic.")),'A new explanation.')
   self.assertEqual(len(model.seen),1)
 async def test_ack_is_not_permission_or_cancellation_of_pending_action(self):
  from unittest.mock import AsyncMock,patch
  async with f.Registry(self.events,self.cfg()) as r:
   router,model=self.router(r);pending={'sentinel':'uncommitted'};router.actions.pending=pending
   with patch.object(router,'confirmation',new=AsyncMock(return_value='Please confirm or cancel.')) as confirm:
    self.assertEqual(await router.answer(f.history("Don't worry.")),'Please confirm or cancel.')
    confirm.assert_awaited_once();self.assertTrue(confirm.call_args.kwargs['clarify_only'])
   self.assertIs(router.pending,pending);self.assertEqual(model.seen,[]);self.assertEqual(r.journal.records,{})
 async def test_provider_json_generation_failure_preserves_completed_read_without_retry(self):
  async with f.Registry(self.events,self.cfg('extension')) as r:
   router,model=self.router(r,f.decision('configured__count_words',{'text':'alpha beta'}),ProviderError(400,'json_validate_failed'))
   reply=await router.answer(f.history('Count alpha beta'))
   self.assertIn('not retry automatically',reply);self.assertEqual(len(model.seen),2);self.assertEqual(r.calls,1);self.assertEqual(r.journal.records,{})

class PrivateFailureTests(unittest.TestCase):
 def test_failed_generation_classifies_structure_and_schema_without_retaining_values(self):
  schema=response_schema([])
  for generated,kind,valid in [('', 'absent',None),('private malformed','malformed',None),(json.dumps({'speech':'private words'}),'object',True),(json.dumps({'speech':'private words','calls':[]}),'object',False)]:
   raw=json.dumps({'error':{'code':'json_validate_failed','failed_generation':generated}})
   meta=error_metadata(raw,[],schema);self.assertEqual(meta['failed_json_kind'],kind)
   if valid is not None:self.assertEqual(meta['failed_json_schema_valid'],valid)
   self.assertNotIn('private',str(meta));self.assertNotIn('speech',str(meta));self.assertNotIn('failed_generation',meta)
