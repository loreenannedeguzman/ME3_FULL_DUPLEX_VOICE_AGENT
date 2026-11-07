"""Native wire mapping and real local HTTP/native-Onit checks; no hosted calls."""
import asyncio,copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import aiohttp
from aiohttp import web
from task_mcp.client import ROOT
from onit_candidate import gemini_native as n
from onit_candidate import gemini_provider as p
from onit_candidate.protocol import CandidateError,validate_response
from onit_candidate.checks import Events,Budget,FixtureRegistry,DECL,native
from onit_candidate.engine import Engine
from onit_candidate.output_quota import OutputQuota
from me3_assistant.admission import Admission
def raw_response(parts=None,finish='STOP'):
 return dict(modelVersion=n.GEMINI_MODEL,candidates=[dict(content=dict(role='model',parts=parts or [dict(text='The fixture value is seven.')]),finishReason=finish)],usageMetadata=dict(promptTokenCount=20,candidatesTokenCount=7,totalTokenCount=27))
def payload():return dict(model=n.GEMINI_MODEL,messages=[dict(role='system',content='Host instruction.'),dict(role='user',content='A current request.')],max_tokens=128,tools=[dict(type='function',function=copy.deepcopy(DECL))],tool_choice='auto')
class MappingChecks(unittest.TestCase):
 def test_exact_schemas_current_request_and_output_bound_survive_mapping(self):
  source=payload();before=copy.deepcopy(source);wire=n.encode_request(source);self.assertEqual(source,before);self.assertEqual(wire['tools'][0]['functionDeclarations'][0]['parametersJsonSchema'],DECL['parameters']);self.assertEqual(wire['generationConfig']['maxOutputTokens'],128);self.assertEqual(wire['contents'][-1]['parts'][0]['text'],'A current request.')
 def test_function_response_preserves_name_args_native_identity_and_opaque_signature(self):
  data=n.decode_response(raw_response([dict(functionCall=dict(name=DECL['name'],args=dict(value=7),id='provider-native-1'),thoughtSignature='OPAQUE_SIGNATURE')]));validate_response(data,expected_model=n.GEMINI_MODEL);call=data['choices'][0]['message']['tool_calls'][0];source=payload();source['messages'] +=[data['choices'][0]['message'],dict(role='tool',tool_call_id=call['id'],content='Seven.')];wire=n.encode_request(source)
  self.assertEqual(wire['contents'][-2]['parts'][0]['thoughtSignature'],'OPAQUE_SIGNATURE');self.assertEqual(wire['contents'][-2]['parts'][0]['functionCall']['id'],'provider-native-1');self.assertEqual(wire['contents'][-1]['parts'][0]['functionResponse'],dict(name=DECL['name'],id='provider-native-1',response=dict(result='Seven.')))
 def test_unknown_response_identity_last_model_turn_or_unsupported_format_rejected(self):
  for change in [lambda v:v['messages'].append(dict(role='tool',tool_call_id='unknown',content='Untrusted.')),lambda v:v['messages'].append(dict(role='assistant',content='Old response.')),lambda v:v.update(response_format={'type':'json_object'})]:
   value=payload();change(value)
   with self.assertRaises(CandidateError):n.encode_request(value)
 def test_parallel_function_results_are_one_user_turn_with_exact_identities_and_order(self):
  data=n.decode_response(raw_response([dict(functionCall=dict(name=DECL['name'],args=dict(value=i),id='native-'+str(i))) for i in [7,8]]));source=payload();source['messages'].append(data['choices'][0]['message'])
  for call in data['choices'][0]['message']['tool_calls']:source['messages'].append(dict(role='tool',tool_call_id=call['id'],content='Result.'))
  wire=n.encode_request(source);self.assertEqual(len(wire['contents']),3);self.assertEqual([p['functionResponse']['id'] for p in wire['contents'][-1]['parts']],['native-7','native-8'])
 def test_finish_reasons_and_invalid_native_arguments_cannot_become_success(self):
  for data in [raw_response(finish='SAFETY'),raw_response([dict(functionCall=dict(name=DECL['name'],args='not-an-object'))]),raw_response([dict(functionCall=dict(name=DECL['name'],args={} ))],finish='MAX_TOKENS')]:
   with self.assertRaises(CandidateError):n.decode_response(data)
 def test_partial_plain_text_retains_length_instead_of_fake_stop(self):
  result=n.decode_response(raw_response(finish='MAX_TOKENS'));self.assertEqual(result['choices'][0]['finish_reason'],'length')
 def test_hidden_thought_is_not_answer_but_all_output_usage_remains_counted(self):
  data=raw_response([dict(text='PRIVATE_REASONING',thought=True),dict(text='A useful final answer.')]);data['usageMetadata'].update(thoughtsTokenCount=11,totalTokenCount=38);result=n.decode_response(data);self.assertEqual(result['choices'][0]['message']['content'],'A useful final answer.');self.assertEqual(result['usage']['completion_tokens'],18);self.assertEqual(result['usage']['total_tokens'],38)
 def test_mismatched_model_or_missing_usage_rejected_without_invented_zero(self):
  for key,value in [('modelVersion','different-model'),('usageMetadata',{})]:
   data=raw_response();data[key]=value
   with self.assertRaises(CandidateError):n.decode_response(data)
 def test_native_endpoint_credentials_are_headers_only_and_no_fallback_route(self):
  t=n.Transport(root=ROOT,credential='PRIVATE_FIXTURE_KEY',admission=Admission(),output=OutputQuota(),emit=lambda *a,**kw:None);endpoint,wire,headers=t.wire_request(payload());self.assertTrue(endpoint.endswith(':generateContent'));self.assertEqual(headers,{'x-goog-api-key':'PRIVATE_FIXTURE_KEY'});self.assertNotIn('PRIVATE_FIXTURE_KEY',endpoint+json.dumps(wire));self.assertNotIn('openai',endpoint)
 def test_interrupted_or_unanswered_history_preserves_user_text_without_fabricated_reply(self):
  for blank in ['',None]:
   source=payload();source['messages'] += [dict(role='assistant',content=blank),dict(role='user',content='A replacement request.')];before=copy.deepcopy(source);wire=n.encode_request(source)
   self.assertEqual(source,before);self.assertEqual([c['role'] for c in wire['contents']],['user','user']);self.assertEqual([c['parts'][0]['text'] for c in wire['contents']],['A current request.','A replacement request.'])
 def test_empty_current_turns_and_invalid_empty_roles_still_fail_closed(self):
  for role in ['assistant','user']:
   source=payload();source['messages'].append(dict(role=role,content=''))
   with self.assertRaises(CandidateError):n.encode_request(source)
 def test_blank_history_cannot_remove_native_calls_signatures_or_result_identity(self):
  source=payload();data=n.decode_response(raw_response([dict(functionCall=dict(name=DECL['name'],args=dict(value=7),id='native-proof'),thoughtSignature='OPAQUE')]));call=data['choices'][0]['message']['tool_calls'][0]
  source['messages'] += [dict(role='assistant',content=''),dict(role='user',content='Inspect.'),data['choices'][0]['message'],dict(role='tool',tool_call_id=call['id'],content='Seven.')]
  wire=n.encode_request(source);self.assertEqual(wire['contents'][-2]['parts'][0]['thoughtSignature'],'OPAQUE');self.assertEqual(wire['contents'][-1]['parts'][0]['functionResponse']['id'],'native-proof')
class NativeHTTPChecks(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.hits=[];self.mode='success';self.events=Events();self.admission=Admission();self.output=OutputQuota();self.t=n.Transport(root=ROOT,credential='PRIVATE_FIXTURE_KEY',admission=self.admission,output=self.output,emit=self.events.emit)
  async def handle(request):
   self.assertEqual(request.headers.get('x-goog-api-key'),'PRIVATE_FIXTURE_KEY');self.assertNotIn('Authorization',request.headers);wire=await request.json();self.hits.append(wire)
   if self.mode=='429':return web.json_response(dict(error=dict(status='RESOURCE_EXHAUSTED')),status=429,headers={'Retry-After':'1'})
   if self.mode=='native-loop' and len(self.hits)==1:return web.json_response(raw_response([dict(functionCall=dict(name=DECL['name'],args=dict(value=7)),thoughtSignature='FIXTURE_OPAQUE')]))
   return web.json_response(raw_response())
  app=web.Application();app.router.add_post('/',handle);self.runner=web.AppRunner(app,access_log=None);await self.runner.setup();site=web.TCPSite(self.runner,'127.0.0.1',0);await site.start();self.url='http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1])+'/';self.patch=patch.object(n,'ENDPOINT',self.url);self.patch.start();self.session=aiohttp.ClientSession(trace_configs=[self.t.network_trace()]);self.t.session=self.session;self.t.owned=False
 async def asyncTearDown(self):self.patch.stop();await self.t.close();await self.session.close();await self.runner.cleanup()
 async def test_native_HTTP_is_normalized_and_counted_once(self):
  result=await self.t.request('reasoning',json=dict(model=n.GEMINI_MODEL,messages=payload()['messages'],max_completion_tokens=128));validate_response(result,expected_model=n.GEMINI_MODEL);self.assertEqual(len(self.hits),1);self.assertEqual(len(self.output.memory),1);self.assertEqual(self.output.memory[0]['actual'],7);self.assertFalse(self.t.active);self.assertNotIn('PRIVATE_',json.dumps(self.events.rows))
 async def test_native_HTTP_429_preserves_unknown_usage_and_never_falls_back(self):
  self.mode='429'
  with self.assertRaises(p.ProviderError):await self.t.request('reasoning',json=dict(model=n.GEMINI_MODEL,messages=payload()['messages'],max_completion_tokens=128))
  self.assertEqual(len(self.hits),1);self.assertIsNone(self.output.memory[0]['actual']);self.assertIsNone(self.admission.memory['roles']['reasoning']['lease']);self.assertFalse(self.t.active)
 async def test_original_Onit_replacement_after_cancelled_history_retains_context(self):
  self.mode='native-loop';registry=FixtureRegistry();budget=Budget()
  with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='native-interrupted-history-') as tmp:
   folder=Path(tmp);engine=Engine(ROOT,registry,self.t,budget,self.events)
   result=await engine.answer('Inspect the fixture value.',folder/'work',folder/'evidence',history=[dict(task='An earlier unanswered request.',response='')],context_state=dict(history_delivery=[dict(state='cancelled',complete_reply_recorded=False)]),lazy_tools=False)
   self.assertEqual(result['answer'],'The fixture value is seven.');self.assertEqual((registry.calls,budget.used,len(self.hits)),(1,2,2));self.assertTrue(any('An earlier unanswered request.' in p.get('text','') for c in self.hits[0]['contents'] for p in c['parts']));self.assertFalse(any(not c['parts'] for c in self.hits[0]['contents']))
 async def test_original_Onit_loop_native_wire_function_result_and_complete_prose(self):
  self.mode='native-loop';registry=FixtureRegistry();budget=Budget()
  with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='native-wire-loop-') as tmp:
   folder=Path(tmp);engine=Engine(ROOT,registry,self.t,budget,self.events);result=await engine.answer('Inspect the fixture value.',folder/'work',folder/'evidence',lazy_tools=False)
   self.assertEqual(result['answer'],'The fixture value is seven.');self.assertEqual((registry.calls,budget.used,len(self.hits)),(1,2,2));self.assertEqual(self.hits[1]['contents'][-1]['parts'][0]['functionResponse']['name'],DECL['name']);self.assertTrue(any(p.get('thoughtSignature')=='FIXTURE_OPAQUE' for c in self.hits[1]['contents'] for p in c['parts']));self.assertIsNone(engine.task)
