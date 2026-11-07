"""No credentials, sockets, owner stores or provider calls; injectable runtime."""
import asyncio,copy,json,sys,unittest
from pathlib import Path
from unittest.mock import patch
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P))
from me3_openai import transport as t

def drive(coro):
    try:coro.send(None)
    except StopIteration as done:return done.value
    raise AssertionError('unexpected real IO')
def payload():return dict(model=t.MODEL,messages=[dict(role='user',content='Explain a triangle.')],tools=[])
def event(name,n,**values):return ('event: '+name+'\ndata: '+json.dumps(dict(type=name,sequence_number=n,**values))+'\n\n').encode()
def raw(index=0):
    item=dict(id='msg_'+str(index),type='message',status='completed',role='assistant',content=[dict(type='output_text',text='A triangle has three sides.')])
    response=dict(id='resp_fixture_'+str(index),model=t.MODEL,status='completed',output=[],usage=dict(input_tokens=10,output_tokens=7,total_tokens=17,output_tokens_details=dict(reasoning_tokens=2)))
    return event('response.output_item.added',0,output_index=0,item={**item,'status':'in_progress'})+event('response.output_item.done',1,output_index=0,item=item)+event('response.completed',2,response=response)
class Ledger:
    def __init__(self):self.rows=[]
    def reserve(self,ordinal,model,**kwargs):self.rows.append(('reserve',ordinal));return ordinal
    def dispatch(self,ticket):self.rows.append(('dispatch',ticket))
    def complete(self,ticket,**kwargs):self.rows.append(('complete',ticket))
    def fail(self,ticket,*args):self.rows.append(('fail',ticket))
    def interrupt(self,ticket,*args):self.rows.append(('interrupt',ticket))
class Response:
    status=200;content_type='application/octet-stream'
    def __init__(self,value):self.value=value;self.content=self;self.closed=False
    async def iter_chunked(self,_):yield self.value
    async def __aenter__(self):return self
    async def __aexit__(self,*_):self.close()
    def close(self):self.closed=True
class Session:
    def __init__(self):self.calls=[];self.responses=[]
    def post(self,url,**kwargs):
        self.calls.append(kwargs);value=Response(raw(len(self.calls)));self.responses.append(value);return value
class Store:
    def __init__(self,expiry=1000):
        self.choice=dict(client_id='fixture-client',issuer=t.auth.ISSUER,subject='fixture-subject',model_slug=t.MODEL)
        self.row=dict(client_id='fixture-client',issuer=t.auth.ISSUER,subject='fixture-subject',plan_enabled=True,access_expires_at=expiry);self.refreshes=0
    def selection(self):return copy.deepcopy(self.choice)
    def accounts(self):return [copy.deepcopy(self.row)]
    def refresh(self,client_id,post,jwks):self.refreshes+=1;self.row['access_expires_at']=2000;return copy.deepcopy(self.row)
    def access_token(self,client_id):return 'private-fixture-token'
class HTTP:
    def __init__(self):self.discoveries=0
    def discovery(self):self.discoveries+=1;return {'keys':[]}
    def post(self,*_):raise AssertionError('fake Store must never call network')
class ImmediateTask:
    def __init__(self,coro):self.value=drive(coro)
    def add_done_callback(self,callback):callback(self)
    def done(self):return True
    def cancelled(self):return False
    def exception(self):return None
    def __await__(self):
        if False:yield
        return self.value

class Checks(unittest.TestCase):
    def make(self,**kwargs):
        self.ledger=Ledger();self.session=Session();self.events=[]
        return t.Transport(lambda name,**values:self.events.append((name,values)),model=kwargs.pop('model',t.MODEL),get_token=lambda:'private-fixture-token',ledger=self.ledger,account_sha256='a'*64,session=self.session,**kwargs)
    def test_two_goals_can_return_identical_text_without_reusing_reasoning(self):
        tr=self.make()
        for n in (31,32):
            tr.begin_goal('same-durable-goal');tr.bind_reservation(n)
            result=drive(tr.request('reasoning',json=payload()))
            self.assertEqual(result['choices'][0]['message']['content'],'A triangle has three sides.')
            self.assertEqual(len(tr.codec._replay),1)
        self.assertEqual(self.ledger.rows,[('reserve',31),('dispatch',31),('complete',31),('reserve',32),('dispatch',32),('complete',32)])
        self.assertEqual(len(self.session.calls),2)
        self.assertTrue(all(v.closed for v in self.session.responses));self.assertNotIn('private-fixture-token',json.dumps(self.events))
    def test_goal_reset_and_reservation_are_host_only_once_per_dispatch(self):
        tr=self.make()
        with self.assertRaises(t.TransportError):tr.bind_reservation(1)
        tr.begin_goal('goal');tr.bind_reservation(31)
        with self.assertRaises(t.TransportError):tr.bind_reservation(32)
        with self.assertRaises(t.TransportError):tr.begin_goal('new-goal')
        drive(tr.request('reasoning',json=payload()))
        with self.assertRaises(t.TransportError):drive(tr.request('reasoning',json=payload()))
        with self.assertRaises(t.TransportError):tr.bind_reservation(31)
        self.assertEqual(len(self.session.calls),1)
    def test_no_goal_or_model_substitution_and_no_binding_from_payload(self):
        tr=self.make()
        with self.assertRaises(t.TransportError):drive(tr.request('reasoning',json={**payload(),'ordinal':99}))
        self.assertEqual(self.ledger.rows,[])
        with self.assertRaises(t.TransportError):self.make(model='different-model')
    def test_cancellation_after_async_token_refresh_prevents_dispatch(self):
        tr=self.make();tr.begin_goal('goal');tr.bind_reservation(31)
        signal=asyncio.Event();tr.stop_requested=signal
        async def token():signal.set();return 'private-fixture-token'
        tr.get_token=token
        with self.assertRaises(asyncio.CancelledError):drive(tr.request('reasoning',json=payload()))
        self.assertEqual(self.session.calls,[]);self.assertEqual(self.ledger.rows,[('reserve',31),('interrupt',31)])
        tr.end_goal();tr.stop_requested=asyncio.Event();tr.get_token=lambda:'private-fixture-token'
        tr.begin_goal('new-owner-goal');tr.bind_reservation(32)
        self.assertTrue(drive(tr.request('reasoning',json=payload()))['choices'])
    def test_credentials_refresh_uses_worker_and_same_account_only(self):
        store=Store(900);http=HTTP();value=t.Credentials(store=store,http=http,clock=lambda:1000)
        workers=[]
        async def worker(fn):workers.append(fn);return fn()
        with patch.object(t.asyncio,'to_thread',worker),patch.object(t.asyncio,'create_task',ImmediateTask),patch.object(t.asyncio,'shield',lambda task:task):
            self.assertEqual(drive(value.token()),'private-fixture-token')
        self.assertEqual((len(workers),store.refreshes,http.discoveries),(1,1,1))
        store.choice['subject']='different-account'
        with self.assertRaises(t.TransportError):value._token()
        self.assertEqual(store.refreshes,1)
    def test_interrupted_refresh_wait_reuses_one_shielded_worker(self):
        value=t.Credentials(store=Store(900),http=HTTP(),clock=lambda:1000);created=[]
        class Pending:
            ready=False
            def __init__(self,coro):coro.close();created.append(self);self.callbacks=[]
            def add_done_callback(self,callback):self.callbacks.append(callback)
            def done(self):return self.ready
            def cancelled(self):return False
            def exception(self):return None
            def finish(self):
                self.ready=True
                for callback in self.callbacks:callback(self)
            def __await__(self):
                if not self.ready:yield 'private-worker-pending'
                return 'private-fixture-token'
        with patch.object(t.asyncio,'create_task',Pending),patch.object(t.asyncio,'shield',lambda task:task):
            first=value.token();self.assertEqual(first.send(None),'private-worker-pending')
            with self.assertRaises(asyncio.CancelledError):first.throw(asyncio.CancelledError())
            second=value.token();self.assertEqual(second.send(None),'private-worker-pending');self.assertEqual(len(created),1)
            created[0].finish()
            with self.assertRaises(StopIteration) as done:second.send(None)
            self.assertEqual(done.exception.value,'private-fixture-token');self.assertIsNone(value._worker)
    def test_local_capacity_metadata_has_no_HTTP_or_counter_effect(self):
        store=Store(900);http=HTTP()
        with patch.object(t.auth,'AuthStore',return_value=store),patch.object(t,'HTTPS',return_value=http):
            self.assertEqual(t.selected_profile('/authored-unused-root'),dict(provider=t.PROVIDER,model=t.MODEL))
            status=t.capacity_snapshot('/authored-unused-root')
        self.assertTrue(status['ready']);self.assertTrue(status['refresh_required']);self.assertFalse(status['provider_capacity_verified'])
        self.assertEqual((status['network_calls'],store.refreshes,http.discoveries),(0,0,0))
    def test_end_goal_drops_unstarted_binding_without_refund_or_reuse(self):
        tr=self.make();tr.begin_goal('old');tr.bind_reservation(31)
        tr.end_goal();self.assertEqual(self.ledger.rows,[])
        tr.begin_goal('new')
        with self.assertRaises(t.TransportError):tr.bind_reservation(31)
        tr.bind_reservation(32);drive(tr.request('reasoning',json=payload()))
        self.assertEqual(self.ledger.rows,[('reserve',32),('dispatch',32),('complete',32)])
    def test_provider_failure_remains_poisoned_after_local_goal_cleanup(self):
        tr=self.make();tr.begin_goal('old');tr.bind_reservation(31)
        original=self.session.post
        def rejected(*args,**kwargs):
            response=original(*args,**kwargs);response.status=503;return response
        self.session.post=rejected
        with self.assertRaises(t.TransportError):drive(tr.request('reasoning',json=payload()))
        tr.end_goal()
        with self.assertRaises(t.TransportError):tr.begin_goal('automatic-retry-refused')
        self.assertEqual(len(self.session.calls),1)

if __name__=='__main__':unittest.main(verbosity=2)
