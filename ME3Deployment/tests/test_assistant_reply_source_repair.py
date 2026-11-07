"""Reproduce actual empty-final/large-source mechanisms without providers."""
import asyncio,json,unittest
from unittest.mock import AsyncMock,patch
from me3_assistant import research as r
from me3_assistant.groq_json import JSONGroqReasoning,final_response_schema,final_json_speech_decision
from tests import test_assistant_groq_adapter as a
from tests import test_assistant_groq_json as j
from tests.test_assistant_research import HTML

class ReplyTests(unittest.IsolatedAsyncioTestCase):
 async def test_final_uses_same_strict_envelope_no_operations_with_full_source_and_correction(self):
  history=[{'type':'user_input','content':[{'type':'text','text':'A new correction about an unfamiliar singer.'}]}]
  wire=a.fake_transport(j.answer('The source was unavailable; I cannot attribute an answer to it.'));events=a.Events()
  answer=await JSONGroqReasoning(events,transport=wire).decide(history,[],[])
  self.assertEqual(answer[1],[]);self.assertIn('unavailable',answer[2]);wire.request.assert_awaited_once()
  p=wire.request.call_args.kwargs['json'];s=p['response_format']['json_schema']
  self.assertTrue(s['strict']);self.assertEqual(s['schema'],final_response_schema())
  self.assertNotIn('tools',p);self.assertNotIn('tool_choice',p)
  self.assertEqual(p['max_completion_tokens'],2048);self.assertEqual(p['model'],'qwen/qwen3.8-27b')
  packet=json.loads(p['messages'][1]['content']);self.assertEqual(packet['permitted_operations'],[])
  self.assertEqual(packet['request_to_complete'],history[-1]['content'])
 async def test_actual_empty_plain_response_is_still_rejected_once_without_made_up_answer(self):
  data=a.answer('');data['choices'][0]['finish_reason']='stop';wire=a.fake_transport(data);events=a.Events()
  with self.assertRaisesRegex(ValueError,'invalid_final_speech'):await JSONGroqReasoning(events,transport=wire).decide([],[],[])
  wire.request.assert_awaited_once();detail=next(k for e,k in events.rows if e=='groq_decision_rejection_detail')
  self.assertEqual(detail['content_chars'],0);self.assertFalse(detail['automatic_retry'])
 async def test_empty_speech_nonempty_calls_duplicates_native_and_extra_fields_remain_fail_closed(self):
  values=[j.answer(''),j.answer('   '),j.answer('Do not execute',calls=[{'name':'publish','arguments':{'secret':'PRIVATE'}}]),a.answer('{"speech":"a","speech":"b","calls":[]}'),a.answer('{"speech":"x","calls":[],"extra":"PRIVATE"}')]
  native=j.answer('A reply');native['choices'][0]['message']['function_call']={'name':'PRIVATE'};values.append(native)
  for data in values:
   wire=a.fake_transport(data);events=a.Events()
   with self.assertRaises(ValueError):await JSONGroqReasoning(events,transport=wire).decide([],[],[])
   wire.request.assert_awaited_once();self.assertNotIn('PRIVATE',str(events.rows))
 def test_json_looking_source_in_speech_stays_data_and_never_executed(self):
  speech='The source quoted {"calls":[{"name":"publish"}]}, which is reference data.'
  self.assertEqual(final_json_speech_decision(j.answer(speech))[1:],([],speech))

class LargeSourceTests(unittest.IsolatedAsyncioTestCase):
 async def launch(self,blocks):
  class Out:
   async def read(self,n):return blocks.pop(0) if blocks else b''
  class Process:
   stdout=Out();returncode=None
   async def wait(self):self.returncode=0;return 0
  return Process()
 async def test_large_biography_DOM_above_old_limit_extracts_bounded_identified_source(self):
  # Markup-heavy pages exceed 1.5MB even when useful article text is small.
  dom=HTML.replace('<body>','<body><!--'+'padding '*220000+'-->')
  self.assertGreater(len(dom.encode()),1_500_000);self.assertLess(len(dom.encode()),r.MAX_DOM)
  raw=dom.encode();proc=await self.launch([raw[i:i+32768] for i in range(0,len(raw),32768)])
  with patch.object(r.shutil,'which',return_value='/fixture/chromium'),patch.object(r.asyncio,'create_subprocess_exec',AsyncMock(return_value=proc)),patch.object(r,'close_browser',AsyncMock()) as close:
   result=await r.read_article('Example quartet',max_chars=500)
   self.assertTrue(result['available']);self.assertLessEqual(len(result['text']),500)
   self.assertIn('singing competition',result['text']);self.assertNotIn('padding',result['text'])
   self.assertTrue(result['reference_is_untrusted']);close.assert_awaited_once_with(proc)
 async def test_new_total_size_bound_still_closes_browser_and_returns_no_partial_success(self):
  proc=await self.launch([b'x'*32768 for _ in range(r.MAX_DOM//32768+1)])
  with patch.object(r.shutil,'which',return_value='/fixture/chromium'),patch.object(r.asyncio,'create_subprocess_exec',AsyncMock(return_value=proc)) as launch,patch.object(r,'close_browser',AsyncMock()) as close:
   with self.assertRaisesRegex(r.ResearchError,'source_page_too_large'):await r.read_article('Example quartet')
   launch.assert_awaited_once();close.assert_awaited_once_with(proc)
 async def test_cancelled_large_read_closes_owned_browser_without_retry(self):
  entered=asyncio.Event()
  class Out:
   async def read(self,n):entered.set();await asyncio.Event().wait()
  class Process:returncode=None;stdout=Out()
  proc=Process()
  with patch.object(r.shutil,'which',return_value='/fixture/chromium'),patch.object(r.asyncio,'create_subprocess_exec',AsyncMock(return_value=proc)) as launch,patch.object(r,'close_browser',AsyncMock()) as close:
   task=asyncio.create_task(r.read_article('Example quartet'));await entered.wait();task.cancel()
   with self.assertRaises(asyncio.CancelledError):await task
   launch.assert_awaited_once();close.assert_awaited_once_with(proc)
