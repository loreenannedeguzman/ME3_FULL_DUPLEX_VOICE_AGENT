import asyncio,json,sqlite3,tempfile,unittest,uuid
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from task_mcp.store import ShoppingStore,item_value,quantity_value,state_digest
from task_mcp.server import TaskService
from task_mcp.reasoner import TaskReasoner,parse_item
from task_mcp.client import TaskClient,TaskError
from voice_agent import ROOT,EventLog

class Events:
    def __init__(self):self.rows=[]
    def emit(self,event,**fields):self.rows.append({'event':event,**fields})

class StoreTests(unittest.TestCase):
    def setUp(self):
        (ROOT/'.tmp').mkdir(exist_ok=True);self.temp=tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='m4-store-');self.store=ShoppingStore(Path(self.temp.name)/'test.sqlite3');self.op=uuid.uuid4().hex
    def tearDown(self):self.temp.cleanup()
    def test_confirmation_proposal_is_not_an_insert(self):
        service=TaskService(self.store);p=service.prepare('notebooks',2,self.op)
        self.assertEqual(self.store.list_items(),[])
        for token,confirmation in [('wrong','confirm'),(p['token'],'yes'),(p['token'],False)]:
            with self.assertRaises(ValueError):service.commit(self.op,token,confirmation)
        self.assertEqual(self.store.list_items(),[])
    def test_write_and_fresh_read_persist_across_instances(self):
        self.store.add('notebooks',2,self.op)
        other=ShoppingStore(self.store.path);row=other.read(self.op)
        self.assertEqual((row['item'],row['quantity']),('notebooks',2));self.assertEqual(len(other.list_items()),1)
    def test_idempotency_and_operation_conflict(self):
        self.store.add('notebooks',2,self.op);self.assertTrue(self.store.add('notebooks',2,self.op)['duplicate'])
        with self.assertRaisesRegex(ValueError,'operation_conflict'):self.store.add('pens',2,self.op)
        self.assertEqual(len(self.store.list_items()),1)
    def test_one_write_budget_and_duplicate_item(self):
        self.store.add('notebooks',2,self.op)
        with self.assertRaisesRegex(ValueError,'task_write_budget'):self.store.add('pens',2,uuid.uuid4().hex)
        other=ShoppingStore(self.store.path)
        with self.assertRaisesRegex(ValueError,'item_already_exists'):other.add('notebooks',2,uuid.uuid4().hex)
        self.assertEqual(len(other.list_items()),1)
    def test_arguments_and_parameterized_storage(self):
        for value in ('','../outside','x'*61,'milk\x00','milk; DROP TABLE items',None):
            with self.assertRaises(ValueError):item_value(value)
        for value in (0,21,-1,True,'2',2.0):
            with self.assertRaises(ValueError):quantity_value(value)
        self.store.add("o'neill notebooks",1,self.op);self.assertEqual(self.store.read(self.op)['item'],"o'neill notebooks")
    def test_expired_proposal_does_not_write(self):
        clock=[0];service=TaskService(self.store,lambda:clock[0]);p=service.prepare('notebooks',2,self.op);clock[0]=121
        with self.assertRaisesRegex(ValueError,'proposal_expired'):service.commit(self.op,p['token'],'confirm')
        self.assertEqual(self.store.list_items(),[])
    def test_proposal_conflict_and_capacity(self):
        service=TaskService(self.store);p=service.prepare('notebooks',2,self.op)
        self.assertEqual(service.prepare('notebooks',2,self.op)['token'],p['token'])
        with self.assertRaisesRegex(ValueError,'operation_conflict'):service.prepare('pens',1,self.op)
        for i in range(3):service.prepare('pens',1,uuid.uuid4().hex)
        with self.assertRaisesRegex(ValueError,'proposal_limit'):service.prepare('pens',1,uuid.uuid4().hex)
    def test_locked_database_is_bounded_and_does_not_report_success(self):
        db=sqlite3.connect(self.store.path);db.execute('BEGIN IMMEDIATE')
        try:
            with self.assertRaises(sqlite3.OperationalError):self.store.add('notebooks',2,self.op)
        finally:db.rollback();db.close()
        self.assertIsNone(self.store.read(self.op))

class FakeTasks:
    def __init__(self,store):self.service=TaskService(store);self.calls=[];self.bad_read=False;self.fail_commit=False;self.hold_commit=False;self.started=asyncio.Event();self.release=asyncio.Event()
    async def call(self,tool,generation,**a):
        self.calls.append(tool)
        if tool=='prepare_item':return self.service.prepare(a['item'],a['quantity'],a['operation_id'])
        if tool=='commit_item':
            if self.fail_commit:raise TimeoutError()
            result=self.service.commit(a['operation_id'],a['token'],a['confirmation']);self.started.set()
            if self.hold_commit:await self.release.wait()
            return result
        if tool=='read_item':
            row=self.service.store.read(a['operation_id'])
            if self.bad_read and row:row['quantity']=20
            return {'row':row,'state_digest':state_digest(row),'verification_source':'fresh_readonly_sqlite_connection'}
        return {'rows':self.service.store.list_items(),'verification_source':'fresh_readonly_sqlite_connection'}

class PolicyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='m4-policy-');self.store=ShoppingStore(Path(self.temp.name)/'state.sqlite3');self.tasks=FakeTasks(self.store);self.events=Events();self.ordinary=SimpleNamespace(answer=AsyncMock(return_value='Four.'));self.agent=TaskReasoner(self.ordinary,self.tasks,self.events);self.history=[]
    async def asyncTearDown(self):self.temp.cleanup()
    def user(self,text):self.history.append({'type':'user_input','content':[{'type':'text','text':text}]})
    def model(self,text):self.history.append({'type':'model_output','content':[{'type':'text','text':text}]})
    async def ask(self,text):
        self.user(text);answer=await self.agent.answer(self.history);self.model(answer);return answer
    async def prepare(self):return await self.ask('Add two notebooks to my shopping list.')
    async def test_missing_detail_confirmation_real_write_and_readback(self):
        self.assertIn('What item',await self.ask('Add to my shopping list.'))
        self.assertIn('Say confirm',await self.ask('Two notebooks.'));self.assertEqual(self.store.list_items(),[])
        answer=await self.ask('Confirm.');self.assertIn('independently checked two notebooks',answer)
        self.assertEqual(self.tasks.calls,['prepare_item','commit_item','read_item']);self.assertEqual(len(self.store.list_items()),1)
        self.assertIn('two notebooks',await self.ask('Read my shopping list.'));self.ordinary.answer.assert_not_awaited()
        self.assertNotIn('notebooks',json.dumps(self.events.rows))
    async def test_model_text_cannot_confirm(self):
        await self.prepare();self.model('confirm');answer=await self.ask('What is two plus two?')
        self.assertEqual(answer,'Four.');self.assertNotIn('commit_item',self.tasks.calls);self.assertEqual(self.store.list_items(),[])
    async def test_cancel_and_duplicate_confirmation_do_not_write(self):
        await self.prepare();self.assertIn('Canceled',await self.ask('Cancel.'));await self.ask('Confirm.');self.assertEqual(self.store.list_items(),[])
    async def test_repeated_confirmation_after_success_does_not_duplicate(self):
        await self.prepare();await self.ask('Confirm.');answer=await self.ask('Confirm.')
        self.assertIn('no pending',answer);self.assertEqual(self.tasks.calls.count('commit_item'),1);self.assertEqual(len(self.store.list_items()),1)
    async def test_canceled_prompt_requires_fresh_completed_prompt(self):
        self.user('Add two notebooks to my shopping list.');prompt=await self.agent.answer(self.history)
        self.history.append({'type':'model_output','control_state':'cancelled','content':[{'type':'text','text':'UNSPOKEN'}]})
        answer=await self.ask('Confirm.');self.assertEqual(answer,prompt);self.assertNotIn('commit_item',self.tasks.calls)
        await self.ask('Confirm.');self.assertEqual(len(self.store.list_items()),1)
    async def test_negative_or_ambiguous_confirmation_is_not_accepted(self):
        await self.prepare();await self.ask('Do not confirm it.');self.assertNotIn('commit_item',self.tasks.calls)
    async def test_failed_verification_never_claims_success_and_stops(self):
        await self.prepare();self.tasks.bad_read=True;answer=await self.ask('Confirm.')
        self.assertIn('could not verify',answer);self.assertNotIn('I saved',answer);self.assertTrue(self.agent.failed.is_set())
        calls=len(self.tasks.calls);await self.ask('Confirm.');self.assertEqual(len(self.tasks.calls),calls)
    async def test_timeout_does_not_automatically_retry(self):
        await self.prepare();self.tasks.fail_commit=True;answer=await self.ask('Confirm.')
        self.assertIn('could not verify',answer);self.assertEqual(self.tasks.calls.count('commit_item'),1);self.assertEqual(self.store.list_items(),[])
    async def test_canceled_committed_write_reconciles_without_retry_or_stale_speech(self):
        await self.prepare();self.tasks.hold_commit=True;self.user('Confirm.')
        task=asyncio.create_task(self.agent.answer(self.history));await self.tasks.started.wait();task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.history.append({'type':'model_output','control_state':'cancelled','content':[{'type':'text','text':'UNSPOKEN'}]})
        self.assertIsNotNone(self.agent.uncertain)
        self.assertEqual(await self.ask('What is two plus two?'),'Four.')
        self.assertEqual(self.tasks.calls.count('commit_item'),1);self.assertEqual(len(self.store.list_items()),1)
        self.assertTrue(any(x['event']=='task_cancel_outcome_reconciled' for x in self.events.rows))
    async def test_ordinary_provider_errors_reach_original_controller(self):
        class Quota(Exception):code=429
        self.ordinary.answer.side_effect=Quota()
        self.user('Count from one to ten.')
        with self.assertRaises(Quota):await self.agent.answer(self.history)
        self.assertFalse(self.agent.failed.is_set())
    def test_quantity_grammar_rejects_bad_values(self):
        for text in ('','zero notebooks','21 notebooks','two','../outside'):
            with self.assertRaises(ValueError):parse_item(text)

class RealTaskMcpTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_protocol_confirmation_write_read_and_clean_shutdown(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='m4-protocol-') as temp:
            path=Path(temp)/'tasks.sqlite3';events=EventLog(Path(temp)/'events')
            try:
                async with TaskClient(events,test_db=path) as client:
                    op=uuid.uuid4().hex;p=await client.call('prepare_item',1,item='notebooks',quantity=2,operation_id=op)
                    self.assertEqual(ShoppingStore(path).list_items(),[])
                    await client.call('commit_item',2,operation_id=op,token=p['token'],confirmation='confirm')
                    read=await client.call('read_item',2,operation_id=op)
                    self.assertEqual(read['row']['quantity'],2);self.assertEqual(read['verification_source'],'fresh_readonly_sqlite_connection')
                    duplicate=await client.call('commit_item',2,operation_id=op,token=p['token'],confirmation='confirm');self.assertTrue(duplicate['duplicate'])
                # Independent direct read, outside MCP process/client/tool return.
                with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM items').fetchone()[0],1)
            finally:events.close()
    async def test_real_protocol_invalid_request_creates_no_item(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='m4-invalid-') as temp:
            path=Path(temp)/'tasks.sqlite3';events=EventLog(Path(temp)/'events')
            try:
                async with TaskClient(events,test_db=path) as client:
                    with self.assertRaises(TaskError):await client.call('prepare_item',1,item='../outside',quantity=2,operation_id=uuid.uuid4().hex)
                    self.assertTrue(client.write_blocked)
                self.assertEqual(ShoppingStore(path).list_items(),[])
            finally:events.close()
    async def test_real_protocol_rejects_bad_confirmation_no_side_effect(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='m4-confirm-') as temp:
            path=Path(temp)/'tasks.sqlite3';events=EventLog(Path(temp)/'events')
            try:
                async with TaskClient(events,test_db=path) as client:
                    op=uuid.uuid4().hex;p=await client.call('prepare_item',1,item='notebooks',quantity=2,operation_id=op)
                    with self.assertRaises(TaskError):await client.call('commit_item',2,operation_id=op,token=p['token'],confirmation='no')
                self.assertEqual(ShoppingStore(path).list_items(),[])
            finally:events.close()

class TaskClientBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_blocks_writes_without_hidden_retry(self):
        client=TaskClient(Events());client.session=SimpleNamespace(call_tool=AsyncMock(side_effect=TimeoutError()))
        for _ in range(2):
            with self.assertRaises(TaskError):await client.call('commit_item',1,operation_id='a'*32,token='b'*32,confirmation='confirm')
        self.assertEqual(client.session.call_tool.await_count,1);self.assertTrue(client.write_blocked)
    async def test_cancellation_blocks_new_writes_but_allows_read_reconciliation(self):
        started=asyncio.Event()
        async def wait(*args,**kwargs):started.set();await asyncio.sleep(20)
        client=TaskClient(Events());client.session=SimpleNamespace(call_tool=wait)
        task=asyncio.create_task(client.call('commit_item',1,operation_id='a'*32,token='b'*32,confirmation='confirm'))
        await started.wait();task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertTrue(client.write_blocked)
        async def read(tool,args,**kwargs):
            return SimpleNamespace(isError=False,structuredContent={'ok':True,'server_role':'task','server_pid':1,'generation':args['generation'],'request_id':args['request_id'],'operation_id':args['operation_id'],'row':None},content=[])
        client.session.call_tool=read
        self.assertIsNone((await client.call('read_item',2,operation_id='a'*32))['row'])
    async def test_identity_mismatch_is_rejected(self):
        async def bad(tool,args,**kwargs):
            return SimpleNamespace(isError=False,structuredContent={'ok':True,'server_role':'task','generation':99,'request_id':args['request_id']},content=[])
        client=TaskClient(Events());client.session=SimpleNamespace(call_tool=bad)
        with self.assertRaises(TaskError):await client.call('read_item',1,operation_id='a'*32)
        self.assertTrue(client.write_blocked)
    async def test_request_budget_and_generation_types(self):
        client=TaskClient(Events());client.calls=20
        with self.assertRaises(TaskError):await client.call('list_items',1)
        client.calls=0
        with self.assertRaises(ValueError):await client.call('list_items',True)
