"""Real asyncio deadlines and actual Responses parser; no sockets or accounts.

Synthetic Session applies the captured aiohttp ClientTimeout at 1/1000 scale.
This qualifies host policy/terminal/cancellation behavior, not network latency.
"""
import asyncio,json,sys,time,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from me3_openai import transport as t
SCALE=.001
PRIVATE='private-fixture-token-never-log'

def event(name,n,**values):return ('event: '+name+'\ndata: '+json.dumps(dict(type=name,sequence_number=n,**values))+'\n\n').encode()
ARGS=json.dumps(dict(job_id='synthetic',path='example.py',content="print('Synthetic only')\n"))
def final(n,function=False):
    response=dict(id='resp_timeout_fixture',model=t.MODEL,status='completed',output=[dict(id='msg_fixture',type='message',role='assistant',status='completed',content=[dict(type='output_text',text='Complete synthetic answer.')])],usage=dict(input_tokens=10,output_tokens=7,total_tokens=17,output_tokens_details=dict(reasoning_tokens=2)))
    if function:response['output']=[dict(id='fc_fixture',type='function_call',status='completed',call_id='call_fixture',namespace='me3',name='workspace__write_workspace_file',arguments=ARGS)]
    return event('response.completed',n,response=response)
def payload(function=False):
    value=dict(model=t.MODEL,messages=[dict(role='user',content='Synthetic timeout fixture.')],tools=[])
    if function:value['tools']=[dict(type='function',function=dict(name='workspace__write_workspace_file',description='Synthetic fixture; never executed.',parameters=dict(type='object',properties=dict(job_id=dict(type='string'),path=dict(type='string'),content=dict(type='string')),required=['job_id','path','content'])))]
    return value

class Ledger:
    def __init__(self):self.rows=[]
    def reserve(self,ordinal,model,**kw):self.rows.append(('reserve',ordinal));return ordinal
    def dispatch(self,ticket):self.rows.append(('dispatch',ticket))
    def complete(self,ticket,**kw):self.rows.append(('complete',ticket))
    def fail(self,ticket,*args):self.rows.append(('fail',ticket))
    def interrupt(self,ticket,*args):self.rows.append(('interrupt',ticket))

class Session:
    def __init__(self,*,timeout,trust_env,mode='progress'):
        self.timeout=timeout;self.trust_env=trust_env;self.mode=mode;self.calls=[];self.responses=[];self.closed=False;self.on_chunk=lambda n:None
    def post(self,url,**kwargs):
        self.calls.append((url,kwargs));response=Response(self);self.responses.append(response);return response
    async def close(self):self.closed=True

class Response:
    status=200;content_type='text/event-stream'
    def __init__(self,session):self.session=session;self.content=self;self.closed=False
    async def __aenter__(self):
        self.bound=asyncio.timeout(self.session.timeout.total*SCALE);await self.bound.__aenter__();return self
    async def __aexit__(self,*args):
        try:return await self.bound.__aexit__(*args)
        finally:self.close()
    def close(self):self.closed=True
    async def iter_chunked(self,size):
        mode=self.session.mode
        if mode=='connect':raise t.aiohttp.ConnectionTimeoutError(PRIVATE)
        if mode=='disconnect':raise t.aiohttp.ServerDisconnectedError(PRIVATE)
        count=12 if mode in ('total','function_total') else 6
        for n in range(count):
            delay=.070 if mode=='idle' and n==1 else .020
            try:await asyncio.wait_for(asyncio.sleep(delay),timeout=self.session.timeout.sock_read*SCALE)
            except TimeoutError:raise t.aiohttp.SocketTimeoutError(PRIVATE) from None
            self.session.on_chunk(n)
            yield event('response.function_call_arguments.delta',n,delta=ARGS[n:n+1]) if mode.startswith('function') else event('response.in_progress',n)
        yield final(count,mode.startswith('function'))

class Checks(unittest.TestCase):
    async def run_case(self,mode='progress',cancel=False,total=None):
        ledger=Ledger();events=[];created=[]
        def factory(**kw):
            value=Session(**kw,mode=mode);created.append(value)
            if cancel:value.on_chunk=lambda n:signal.set() if n==1 else None
            return value
        tr=t.Transport(lambda name,**v:events.append((name,v)),get_token=lambda:PRIVATE,ledger=ledger,account_sha256='a'*64)
        signal=asyncio.Event();tr.stop_requested=signal;tr.begin_goal('timeout-fixture');tr.bind_reservation(31)
        begin=time.monotonic();result=None;error=None
        with patch.object(t.aiohttp,'ClientSession',factory):
            try:result=await tr.request('reasoning',json=payload(mode.startswith('function')))
            except BaseException as exc:error=exc
        elapsed=time.monotonic()-begin
        await tr.close()
        self.assertEqual(len(created),1);session=created[0];self.assertEqual(len(session.calls),1)
        self.assertEqual((session.timeout.total,session.timeout.connect,session.timeout.sock_read),(total if total is not None else 180,10,45));self.assertFalse(session.trust_env)
        self.assertTrue(session.closed);self.assertTrue(all(v.closed for v in session.responses));self.assertFalse(tr.active)
        self.assertEqual(session.calls[0][0],t.codec_module.ENDPOINT);self.assertFalse(session.calls[0][1]['allow_redirects'])
        self.assertNotIn(PRIVATE,json.dumps(events));self.assertEqual(sum(v[0]=='reserve' for v in ledger.rows),1)
        if error is not None:self.assertNotIn(PRIVATE,str(error))
        return result,error,ledger,events,elapsed,tr

    def test_progressing_complete_stream_exceeds_old_bound_and_fits_new_bound(self):
        result,error,ledger,events,elapsed,_=asyncio.run(self.run_case())
        self.assertIsNone(error);self.assertIn('Complete synthetic answer.',result['choices'][0]['message']['content'])
        self.assertGreater(elapsed,60*SCALE);self.assertLess(elapsed,180*SCALE)
        self.assertEqual(ledger.rows,[('reserve',31),('dispatch',31),('complete',31)])
        # Identical progressing stream really expires with the old total.
        with patch.object(t,'REQUEST_TOTAL_SECONDS',60):
            _,error,ledger,events,_,_=asyncio.run(self.run_case(total=60))
        self.assertEqual(str(error),'openai_request_total_timeout_no_retry');self.assertEqual(ledger.rows[-1],('fail',31))

    def test_total_timeout_discards_incomplete_stream_and_keeps_one_charge(self):
        result,error,ledger,events,elapsed,tr=asyncio.run(self.run_case('total'))
        self.assertIsNone(result);self.assertEqual(str(error),'openai_request_total_timeout_no_retry')
        self.assertEqual(ledger.rows,[('reserve',31),('dispatch',31),('fail',31)]);self.assertTrue(tr.failed)
        self.assertGreaterEqual(elapsed,.16);self.assertLess(elapsed,.35)
        self.assertEqual(events[-1][1]['error_code'],'openai_request_total_timeout_no_retry')

    def test_partial_function_arguments_never_return_a_tool_before_completed(self):
        result,error,ledger,events,elapsed,_=asyncio.run(self.run_case('function'))
        self.assertIsNone(error);self.assertGreater(elapsed,60*SCALE)
        calls=result['choices'][0]['message']['tool_calls'];self.assertEqual(len(calls),1)
        self.assertEqual(calls[0]['function'],dict(name='workspace__write_workspace_file',arguments=ARGS))
        self.assertEqual(ledger.rows,[('reserve',31),('dispatch',31),('complete',31)])
        result,error,ledger,events,_,tr=asyncio.run(self.run_case('function_total'))
        self.assertIsNone(result);self.assertEqual(str(error),'openai_request_total_timeout_no_retry')
        self.assertEqual(ledger.rows,[('reserve',31),('dispatch',31),('fail',31)])
        self.assertEqual(tr.codec._seen_calls,set(),'partial argument deltas cannot authorize a call')

    def test_idle_timeout_remains_shorter_than_total_and_discards_partial(self):
        result,error,ledger,events,elapsed,_=asyncio.run(self.run_case('idle'))
        self.assertIsNone(result);self.assertEqual(str(error),'openai_stream_idle_timeout_no_retry')
        self.assertLess(elapsed,180*SCALE);self.assertEqual(ledger.rows[-1],('fail',31))
        self.assertEqual(events[-1][1]['error_code'],'openai_stream_idle_timeout_no_retry')

    def test_cancellation_closes_partial_stream_without_failure_or_retry(self):
        result,error,ledger,events,_,tr=asyncio.run(self.run_case(cancel=True))
        self.assertIsNone(result);self.assertIsInstance(error,asyncio.CancelledError);self.assertFalse(tr.failed)
        self.assertEqual(ledger.rows,[('reserve',31),('dispatch',31),('interrupt',31)])
        self.assertEqual(events[-1][1]['error_code'],'request_interrupted')

    def test_connection_and_disconnect_codes_do_not_expose_exception_text(self):
        for mode,expected in (('connect','openai_connect_timeout_no_retry'),('disconnect','openai_stream_disconnected_no_retry')):
            with self.subTest(mode=mode):
                result,error,ledger,events,_,tr=asyncio.run(self.run_case(mode))
                self.assertIsNone(result);self.assertEqual(str(error),expected);self.assertEqual(events[-1][1]['error_code'],expected)
                self.assertEqual(ledger.rows,[('reserve',31),('dispatch',31),('fail',31)]);self.assertTrue(tr.failed)

    def test_classification_is_type_based_and_unknown_values_remain_generic(self):
        self.assertIsNone(t.connection_failure_code(ValueError(PRIVATE)))
        self.assertIsNone(t.connection_failure_code(ValueError('openai_stream_idle_timeout_no_retry')))
        self.assertEqual(t.connection_failure_code(t.aiohttp.ClientConnectorError(None,OSError(PRIVATE))),'openai_connect_failed_no_retry')
        self.assertEqual(t.connection_failure_code(t.aiohttp.ClientConnectionError(PRIVATE)),'openai_connection_failed_no_retry')
        self.assertEqual(t.connection_failure_code(t.aiohttp.ServerTimeoutError(PRIVATE)),'openai_server_timeout_no_retry')
        self.assertEqual(t.auth_failure_code(t.auth.AuthError('auth_store_busy')),'auth_store_busy')

if __name__=='__main__':unittest.main(verbosity=2)
