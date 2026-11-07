import base64,unittest
from me3_assistant import groq_backend as g
from me3_assistant.groq_server import Service
from tests import test_assistant_groq_adapter as a

class LanguageLabelTests(unittest.IsolatedAsyncioTestCase):
 async def test_English_names_and_standard_locale_labels_are_accepted_without_retranslation_or_retry(self):
  for label in ('English','EN',' ENGLISH ','en-US','en_GB','en-Latn-US','eng','eNgLiSh'):
   with self.subTest(label=label):
    wire=a.fake_transport({'text':'A fresh natural question.','language':label});events=a.Events();model=g.GroqASR(events.emit,transport=wire)
    self.assertEqual((await model.transcribe(bytes(32000),'low'))['text'],'A fresh natural question.')
    wire.request.assert_awaited_once();self.assertNotIn('A fresh natural question.',str(events.rows))
 async def test_conflicting_and_malformed_labels_still_stop_before_any_valid_transcript(self):
  for label in ('Indonesian','id','id-ID','french','enough','en-private-value-too-long',[],{'private':'content'},'english private payload','x'*65):
   with self.subTest(label=label):
    wire=a.fake_transport({'text':'private speech','language':label});events=a.Events();model=g.GroqASR(events.emit,transport=wire)
    with self.assertRaisesRegex(ValueError,'asr_language_mismatch'):await model.transcribe(bytes(32000),'low')
    wire.request.assert_awaited_once();self.assertNotIn('private',str(events.rows));self.assertNotIn('content',str(events.rows))
 async def test_actual_MCP_service_returns_English_result_and_cleanup_with_capitalized_metadata(self):
  wire=a.fake_transport({'text':'An unprescribed topic.','language':'English'});events=[]
  service=Service(lambda emit:g.GroqASR(emit,transport=wire),writer=lambda event,**kw:events.append((event,kw)))
  result=await service.call(base64.b64encode(bytes(32000)).decode(),1,'fixture-language-001')
  self.assertTrue(result['ok']);self.assertEqual(result['text'],'An unprescribed topic.');self.assertTrue(result['cleanup_ok']);self.assertEqual(result['input_language'],'en')
  wire.request.assert_awaited_once();self.assertNotIn('An unprescribed topic.',str(events))
 async def test_missing_language_metadata_retains_explicit_English_request(self):
  wire=a.fake_transport({'text':'Still the exact returned text.'});model=g.GroqASR(lambda *a,**k:None,transport=wire)
  self.assertEqual((await model.transcribe(bytes(32000),'low'))['text'],'Still the exact returned text.')
  values={h['name']:v for h,_,v in wire.request.call_args.kwargs['data']._fields};self.assertEqual(values['language'],'en')
