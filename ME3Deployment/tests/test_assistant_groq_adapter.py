import asyncio,json,unittest,os,sys
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,patch
from google.genai import types
from me3_assistant import groq_backend as G

class Events:
    def __init__(self):self.rows=[]
    def emit(self,event,**kw):self.rows.append((event,kw))
class Reply:
    def __init__(self,status=200,parts=None):self.status=status;self.content=NS(read=AsyncMock(side_effect=parts or [b'{"text":"hello"}',b'']))
    async def __aenter__(self):return self
    async def __aexit__(self,*a):pass
class Session:
    def __init__(self,reply):self.reply=reply;self.calls=[]
    def post(self,*a,**kw):self.calls.append((a,kw));return self.reply
def fake_transport(result):return NS(request=AsyncMock(return_value=result),close=AsyncMock())
def answer(text='Hello.',calls=None):return {'choices':[{'message':{'content':text,**({'tool_calls':calls} if calls else {})}}]}
def call(name='fixture',args='{"count":2}'):return {'id':'call_fixture','type':'function','function':{'name':name,'arguments':args}}

class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_usage_and_quota_metadata_are_narrowly_whitelisted(self):
        reply=Reply(parts=[b'{"text":"hello","usage":{"prompt_tokens":20,"completion_tokens":2,"total_tokens":22,"private":"not_logged"}}',b'']);reply.headers={'x-ratelimit-limit-requests':'1000','Authorization':'fixture-secret','x-ratelimit-remaining-tokens':'bad-header'}
        e=Events();t=G.Transport(e.emit,key='fixture-secret',session=Session(reply));await t.request('asr')
        self.assertNotIn('fixture-secret',str(e.rows));self.assertNotIn('not_logged',str(e.rows));self.assertNotIn('bad-header',str(e.rows));self.assertTrue(any(kw.get('total_tokens')==22 for _,kw in e.rows))
    def test_key_alone_cannot_enable_unselected_provider(self):
        with patch.dict(os.environ,{},clear=True),self.assertRaisesRegex(ValueError,'explicit_groq_backend_required'):G.Transport(lambda *a,**kw:None,key='fixture-secret')
    async def test_exact_one_allowlisted_post_and_key_never_logged(self):
        e=Events();s=Session(Reply());t=G.Transport(e.emit,key='fixture-secret',session=s);r=await t.request('asr',data='fixture')
        self.assertEqual(r['text'],'hello');self.assertEqual(len(s.calls),1);self.assertEqual(s.calls[0][0],(G.ENDPOINTS['asr'],));self.assertFalse(s.calls[0][1]['allow_redirects']);self.assertFalse(t.active);self.assertNotIn('fixture-secret',str(e.rows))
    async def test_http_error_never_retries_or_reads_private_error_body(self):
        reply=Reply(429);s=Session(reply);t=G.Transport(lambda *a,**kw:None,key='fixture-secret',session=s)
        with self.assertRaises(G.ProviderError) as e:await t.request('reasoning',json={})
        self.assertEqual(e.exception.code,429);self.assertEqual(len(s.calls),1);reply.content.read.assert_not_awaited();self.assertFalse(t.active)
    async def test_partial_response_reads_are_joined_and_bound_enforced(self):
        t=G.Transport(lambda *a,**kw:None,key='fixture',session=Session(Reply(parts=[b'{"text":',b'"hello"}',b''])))
        self.assertEqual((await t.request('asr'))['text'],'hello')
        t.session=Session(Reply(parts=[b'x'*131073]))
        with self.assertRaisesRegex(ValueError,'too_large'):await t.request('asr')
        self.assertFalse(t.active)
    async def test_unknown_endpoint_or_concurrent_call_never_dispatches(self):
        s=Session(Reply());t=G.Transport(lambda *a,**kw:None,key='fixture',session=s)
        with self.assertRaises(ValueError):await t.request('https://example.invalid')
        t.active=True
        with self.assertRaises(ValueError):await t.request('asr')
        self.assertEqual(s.calls,[])
    async def test_cancellation_releases_active_request(self):
        reply=Reply();entered=asyncio.Event()
        async def stalled(n):entered.set();await asyncio.Event().wait()
        reply.content.read.side_effect=stalled;t=G.Transport(lambda *a,**kw:None,key='fixture',session=Session(reply));task=asyncio.create_task(t.request('asr'));await entered.wait();task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertFalse(t.active)

class SpeechTests(unittest.IsolatedAsyncioTestCase):
    async def test_single_and_split_each_use_one_post_without_synthesis(self):
        transport=fake_transport({'text':'hello'});m=G.GroqASR(lambda *a,**kw:None,transport=transport)
        self.assertEqual((await m.transcribe(b'\0\0'*16000,'low'))['text'],'hello');self.assertEqual(transport.request.await_count,1)
        transport.request.return_value={'text':'hello world','words':[{'word':'hello','start':0,'end':.8},{'word':'world','start':1.1,'end':1.8}]}
        r=await m.transcribe(b'\0\0'*16000,'low',b'\0\0'*16000);self.assertEqual(r,{'previous_text':'hello','text':'world'});self.assertEqual(transport.request.await_count,2)
    async def test_ambiguous_split_is_failure_not_extra_provider_call(self):
        t=fake_transport({'text':'hello','words':[{'word':'hello','start':.9,'end':1.1}]});m=G.GroqASR(lambda *a,**kw:None,transport=t)
        with self.assertRaisesRegex(ValueError,'split_boundary_uncertain'):await m.transcribe(b'\0\0'*16000,'low',b'\0\0'*16000)
        self.assertEqual(t.request.await_count,1)
    async def test_invalid_inputs_fail_before_dispatch(self):
        t=fake_transport({});m=G.GroqASR(lambda *a,**kw:None,transport=t)
        for pcm,profile in ((b'','low'),(b'1','low'),(b'\0\0','minimal')):
            with self.assertRaises(ValueError):await m.transcribe(pcm,profile)
        t.request.assert_not_awaited()
    async def test_actual_mcp_discovery_never_needs_a_credential(self):
        from mcp import ClientSession,StdioServerParameters
        from mcp.client.stdio import stdio_client
        from speech_mcp.client import SpeechClients
        env={k:v for k,v in os.environ.items() if k in {'PATH','SYSTEMROOT','HOME','USERPROFILE','LD_LIBRARY_PATH','PYTHONPATH'}};env['ALLOW_PROVIDER_AUDIO']='0'
        with open(os.devnull,'w') as err:
            async with stdio_client(StdioServerParameters(command=sys.executable,args=['-B','-m','me3_assistant.groq_server','asr'],env=env),errlog=err) as streams:
                async with ClientSession(*streams) as session:
                    await session.initialize();tools=await session.list_tools();self.assertIn('transcribe',{t.name for t in tools.tools})
                    info=SpeechClients.unpack(await session.call_tool('server_info',{}));self.assertEqual(info['provider'],'groq');self.assertEqual(info['model'],G.ASR_MODEL);self.assertEqual(info['active_requests'],0)
    def test_word_alignment_must_cover_whole_transcript_and_valid_time(self):
        for data in ({'text':'hello world','words':[{'word':'hello','start':0,'end':.5}]},{'text':'hello','words':[{'word':'hello','start':0,'end':float('nan')}]},{'text':'hello','words':[]},{'text':'hello','words':[{'word':'hello','start':1,'end':1}]}):
            with self.assertRaises(ValueError):G.transcript(data,1,2)

class ReasoningTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_schema_manual_tool_mode_and_no_builtin_execution(self):
        e=Events();t=fake_transport(answer(calls=[call('new_tool')],text=None));m=G.GroqReasoning(e,transport=t);schema={'type':'object','properties':{'count':{'type':'integer'}},'required':['count'],'additionalProperties':False}
        content,calls,text=await m.decide([{'type':'user_input','content':[{'text':'count this'}]}],[{'name':'new_tool','description':'Fixture','parameters':schema}],[])
        payload=t.request.call_args.kwargs['json'];self.assertEqual(payload['tools'][0]['function']['parameters'],schema);self.assertEqual(payload['model'],G.TEXT_MODEL);self.assertFalse(payload['parallel_tool_calls']);self.assertEqual(calls[0].args,{'count':2});self.assertEqual(calls[0].id,'call_fixture');self.assertFalse(m.lock.locked());self.assertNotIn('count this',str(e.rows))
    def test_tool_call_and_result_ids_survive_next_round(self):
        content,calls,_=G.decision(answer(calls=[call('new_tool')],text=None));result=types.Content(role='user',parts=[types.Part.from_function_response(name='new_tool',response={'result':2})]);out=G.messages([], [content,result])
        self.assertEqual(out[-2]['tool_calls'][0]['id'],out[-1]['tool_call_id']);self.assertEqual(json.loads(out[-1]['content']),{'result':2})
        with self.assertRaisesRegex(ValueError,'missing'):G.messages([],[content])
        with self.assertRaisesRegex(ValueError,'ambiguous'):G.messages([],[result])
    def test_malformed_or_multiple_calls_are_not_executed_or_reported_as_success(self):
        for data in (answer(text=None),answer(calls=[call(args='[]')]),answer(calls=[call(),call()]),{'choices':[]}):
            with self.assertRaises(ValueError):G.decision(data)
    async def test_failure_and_cancel_release_lock_without_retry(self):
        t=fake_transport(answer());m=G.GroqReasoning(Events(),transport=t);t.request.side_effect=G.ProviderError(504)
        with self.assertRaises(G.ProviderError):await m.decide([],[],[])
        self.assertEqual(t.request.await_count,1);self.assertFalse(m.lock.locked())
        entered=asyncio.Event()
        async def stall(*a,**kw):entered.set();await asyncio.Event().wait()
        t.request.side_effect=stall;task=asyncio.create_task(m.decide([],[],[]));await entered.wait();task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertFalse(m.lock.locked())
    def test_private_reasoning_field_never_becomes_spoken_answer(self):
        data=answer('The answer is four.');data['choices'][0]['message']['reasoning']='fixture-private-analysis'
        _,_,text=G.decision(data);self.assertEqual(text,'The answer is four.');self.assertNotIn('private',text)
if __name__=='__main__':unittest.main()
