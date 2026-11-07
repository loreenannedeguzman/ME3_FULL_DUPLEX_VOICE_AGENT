"""Provider-free tests. Fixture model output is never counted as live acceptance."""
import asyncio,hashlib,http.client,json,tempfile,threading,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,AsyncMock
from me3_assistant.gui import Console,Server,conversation,row
from me3_assistant.guard import authorize
from tests.test_assistant_mode import config,Model,decision,FixtureBudget
from tests import test_assistant_mode as fixtures

class TextGrantTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.folder=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def test_reasoning_only_grant_preserves_all_shared_caps(self):
        cfg,src,g,now=fixtures.ConfigTests.grant(self);g.update(mode='text',max_reservations={'asr':0,'reasoning':2,'tts':0,'commit_item':0});p=self.folder/'text.json';p.write_text(json.dumps(g))
        got,limits=authorize(cfg,src,p,self.folder,now,mode='text')
        self.assertEqual(limits,src['budget_limits']);self.assertEqual(got['max_reservations']['tts'],0)
        with self.assertRaises(ValueError):authorize(cfg,src,p,self.folder,now)
    def test_text_rejects_speech_or_write_reservations_and_actions(self):
        for role in ('asr','tts','commit_item','action'):
            cfg,src,g,now=fixtures.ConfigTests.grant(self);g.update(mode='text',max_reservations={'asr':0,'reasoning':1,'tts':0,'commit_item':0})
            if role=='action':g['allowed_actions']=['propose_shopping_item']
            else:g['max_reservations'][role]=1
            p=self.folder/'text.json';p.write_text(json.dumps(g))
            with self.subTest(role=role),self.assertRaises(ValueError):authorize(cfg,src,p,self.folder,now,mode='text')

class ConsoleTests(unittest.IsolatedAsyncioTestCase):
    def console(self):
        cfg=config('extension');cfg['sha256']='fixture';return Console(cfg,{'revision':'fixture'})
    async def test_start_denied_before_task_or_model_or_audio(self):
        c=self.console()
        with patch.object(c,'gate',side_effect=ValueError('no_allowance')):
            with self.assertRaises(ValueError):await c.command({'action':'start_text'})
        self.assertIsNone(c.task);self.assertEqual(c.state,'stopped')
    async def test_answer_must_be_displayed_before_context_commit(self):
        c=self.console();c.mode='text';c.state='ready';c.ready=asyncio.Event();c.ready.set();c.stop_event=asyncio.Event();c.router=SimpleNamespace(answer=AsyncMock(return_value='Visible answer.'),pending=None)
        await c.command({'action':'send','text':'A question'});await c.turn
        self.assertEqual(len(c.history),1);self.assertIsNotNone(c.pending)
        with self.assertRaises(ValueError):await c.command({'action':'send','text':'too early'})
        await c.command({'action':'ack','id':'wrong'});self.assertEqual(len(c.history),1)
        await c.command({'action':'ack','id':c.pending['id']});self.assertEqual(len(c.history),2);self.assertEqual(c.state,'ready')
    async def test_cancel_suppresses_late_result_even_when_model_ignores_cancel(self):
        c=self.console();c.mode='text';c.state='ready';c.ready=asyncio.Event();c.ready.set();c.stop_event=asyncio.Event();started=asyncio.Event()
        async def slow(h):
            started.set()
            try:await asyncio.Event().wait()
            except asyncio.CancelledError:return 'obsolete'
        c.router=SimpleNamespace(answer=slow,pending=None)
        await c.command({'action':'send','text':'old'});await started.wait();await c.command({'action':'cancel'})
        self.assertIsNone(c.pending);self.assertEqual(len(c.history),1);self.assertEqual(c.state,'ready')
    async def test_voice_requires_stop_instead_of_unsafe_text_injection(self):
        c=self.console();c.mode='voice';c.state='listening'
        with self.assertRaises(ValueError):await c.command({'action':'send','text':'hello'})
        with self.assertRaises(ValueError):await c.command({'action':'cancel'})
    async def test_no_private_event_fields_or_control_rows_in_ui(self):
        c=self.console();c.observe('completion_ownership',current='pause_hold',token='private',arguments={'secret':'hidden'})
        self.assertEqual(c.state,'pause_hold');self.assertNotIn('private',json.dumps(c.events));self.assertNotIn('hidden',json.dumps(c.events))
        self.assertEqual(conversation([row('user','hello'),{**row('assistant','internal'),'control_state':'canceled'}]),[{'id':'0','role':'user','text':'hello'}])
    async def test_typed_owner_real_MCP_schema_selection_and_clean_close(self):
        # Real fixture MCP over stdio + fake model; no real task DB or hosted calls.
        c=self.console();c.mode='text';c.stop_event=asyncio.Event();c.ready=asyncio.Event()
        seen=[]
        class Fake(Model):
            def __init__(self,events):super().__init__(decision('configured__count_words',{'text':'one two'}),decision(text='Two words.'))
            async def close(self):seen.append('closed')
        budget=FixtureBudget(2)
        with tempfile.TemporaryDirectory() as d,patch('me3_assistant.gui.NORMAL',Path(d)),patch('me3_assistant.gui.SharedBudget',return_value=budget),patch('me3_assistant.reasoning.Reasoning',Fake),patch('dotenv.load_dotenv'):
            c.task=asyncio.create_task(c.text({'allowed_actions':[]},{}))
            await asyncio.wait_for(c.ready.wait(),15)
            await c.command({'action':'send','text':'Count the words one two'})
            await asyncio.wait_for(c.turn,15)
            self.assertEqual(c.pending['text'],'Two words.');self.assertEqual(budget.used,2)
            self.assertTrue(any(e['event']=='assistant_rpc_end' for e in c.events))
            await c.command({'action':'ack','id':c.pending['id']});await c.stop()
            self.assertEqual(seen,['closed']);self.assertEqual(c.state,'stopped');self.assertIsNone(c.router)

class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.loop=asyncio.new_event_loop();self.worker=threading.Thread(target=self.loop.run_forever,daemon=True);self.worker.start()
        self.console=SimpleNamespace(snapshot=lambda:{'notice':'standby','provider_calls':0},command=AsyncMock(return_value={'ok':True}))
        self.server=Server(('127.0.0.1',0),self.console,self.loop);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.port=self.server.server_port
    def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join();self.loop.call_soon_threadsafe(self.loop.stop);self.worker.join();self.loop.close()
    def request(self,method,path,body=None,**headers):
        c=http.client.HTTPConnection('127.0.0.1',self.port,timeout=5);c.request(method,path,body,headers);r=c.getresponse();data=r.read();h=dict(r.getheaders());status=r.status;c.close();return status,data,h
    def auth(self):return {'Cookie':'me3_gui='+self.server.cookie,'Origin':f'http://127.0.0.1:{self.port}','X-ME3-CSRF':self.server.token,'Content-Type':'application/json'}
    def test_fixed_assets_cookie_csp_and_no_path_escape(self):
        status,body,h=self.request('GET','/');self.assertEqual(status,200);self.assertIn(b"What's on your mind today?",body);self.assertNotIn(b'__CSRF__',body);self.assertIn('HttpOnly',h['Set-Cookie']);self.assertIn("frame-ancestors 'none'",h['Content-Security-Policy'])
        self.assertEqual(self.request('GET','/../.env')[0],404);self.assertEqual(self.request('GET','/api/state')[0],403)
        self.assertEqual(self.request('GET','/api/state',Cookie='me3_gui='+self.server.cookie)[0],200)
    def test_host_origin_csrf_and_oversized_body_rejected_before_command(self):
        self.assertEqual(self.request('GET','/',Host='evil.invalid')[0],403)
        h=self.auth();h['Origin']='https://evil.invalid';self.assertEqual(self.request('POST','/api/command','{}',**h)[0],403)
        h=self.auth();h['X-ME3-CSRF']='wrong';self.assertEqual(self.request('POST','/api/command','{}',**h)[0],403)
        self.assertEqual(self.request('POST','/api/command','x'*32769,**self.auth())[0],400);self.console.command.assert_not_called()
    def test_same_origin_authenticated_command_dispatch(self):
        self.assertEqual(self.request('POST','/api/command',json.dumps({'action':'stop'}),**self.auth())[0],200);self.console.command.assert_awaited_once_with({'action':'stop'})

if __name__=='__main__':unittest.main()
