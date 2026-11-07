"""Real Pi filesystem, namespace, stdio MCP and router; no hosted model or audio."""
import asyncio,hashlib,json,os,sys,tempfile,unittest
from pathlib import Path
from me3_assistant.workspace import Workspace,WorkspaceError,relative,MAX_WRITE
from me3_assistant.workspace_exec import Executor
from me3_assistant.config import load
from me3_assistant.registry import Registry,ToolFailure
from me3_assistant.router import Router
from me3_assistant.actions import Journal
from task_mcp.client import ROOT
from tests.test_assistant_mode import Events,FixtureBudget,decision,history

GAME="""def judge(guess):
    return 'Correct!' if guess == 8 else 'Try again.'

if __name__ == '__main__':
    print('Guess a number from 1 to 10.')
    for attempt in range(3):
        result = judge(int(input()))
        print(result)
        if result == 'Correct!':
            break
"""
TEST="""import unittest
from main import judge
class GameTest(unittest.TestCase):
    def test_correct_guess(self):
        self.assertEqual(judge(7), 'Correct!')
    def test_wrong_guess(self):
        self.assertEqual(judge(3), 'Try again.')
"""

def cfg(folder):
    c={'version':1,'runtime':{'max_session_seconds':600,'max_turns':8,'tool_rounds':8,'batch_calls':3,'rpc_calls':32},'servers':[{'id':'workspace','module':'me3_assistant.workspace_server','args':['--workspace-root',str(folder/'artifacts'),'--runs-root',str(folder/'runs')],'identity':'me3','role':'utility','read_tools':['workspace_capabilities','list_workspace_files','read_workspace_file'],'workspace_tools':['write_workspace_file','run_workspace_command']}],'actions':[]}
    path=folder/'config.json';path.write_text(json.dumps(c),encoding='utf-8');return load(path)

class Paths(unittest.TestCase):
    def test_paths_reject_traversal_secrets_devices_streams_and_shell_syntax(self):
        for path in ['../a.py','/a.py','C:/a.py','a\\b.py','.env','sub/.env','a.py:stream','CON.py','a;echo.py','a/../../b.py','a.py/','x.txt\n','a.','a/b/c/d/e/f/g.py']:
            with self.subTest(path=path),self.assertRaises(WorkspaceError):relative(path)
        self.assertEqual(relative('game/main.py'),['game','main.py'])

@unittest.skipUnless(os.name=='posix','Workspace writes require POSIX directory handles on target Pi')
class Files(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='workspace-files-');self.folder=Path(self.tmp.name);self.w=Workspace(self.folder/'artifacts')
    def tearDown(self):self.tmp.cleanup()
    def test_create_independent_read_cas_and_historical_bytes(self):
        self.assertEqual(self.w.listing()['files'],[]);self.assertFalse(self.w.root.exists())
        first=self.w.write('game/main.py','print(1)');read=self.w.read('game/main.py');self.assertEqual(read['sha256'],first['sha256']);self.assertEqual(read['text'],'print(1)')
        with self.assertRaisesRegex(WorkspaceError,'already_exists'):self.w.write('game/main.py','print(2)')
        with self.assertRaisesRegex(WorkspaceError,'version_changed'):self.w.write('game/main.py','print(2)','a'*64)
        fixed=self.w.write('game/main.py','print(2)',first['sha256']);self.assertTrue(fixed['verified_by_fresh_read']);self.assertEqual((self.w.root/'.versions'/first['sha256']).read_text(),'print(1)');self.assertEqual(len(self.w.listing()['files']),1)
    def test_ancestors_symlinks_hardlinks_and_nonregular_files_rejected(self):
        self.w.write('safe/main.py','print(1)');outside=self.folder/'private.py';outside.write_text('secret')
        (self.w.root/'link.py').symlink_to(outside)
        with self.assertRaises(WorkspaceError):self.w.read('link.py')
        with self.assertRaises(WorkspaceError):self.w.write('link.py','changed')
        (self.w.root/'link.py').unlink();os.link(outside,self.w.root/'hard.py')
        with self.assertRaises(WorkspaceError):self.w.read('hard.py')
        (self.w.root/'hard.py').unlink();(self.folder/'alias').symlink_to(self.w.root,target_is_directory=True)
        with self.assertRaises(WorkspaceError):Workspace(self.folder/'alias').read('safe/main.py')
        os.mkfifo(self.w.root/'pipe.py')
        with self.assertRaises(WorkspaceError):self.w.read('pipe.py')
        self.assertEqual(outside.read_text(),'secret')
    def test_paging_limits_version_and_unicode(self):
        data='é'*2000;r=self.w.write('story.txt',data);p=self.w.read('story.txt')
        self.assertEqual(len(p['text']),1000)
        with self.assertRaisesRegex(WorkspaceError,'version_required'):self.w.read('story.txt',1000)
        self.assertEqual(self.w.read('story.txt',1000,r['sha256'])['text'],data[1000:])
        for text in ['a'*(MAX_WRITE+1),'é'*(MAX_WRITE//2+1),'bad\x00']:
            with self.assertRaises(WorkspaceError):self.w.write('bad.txt',text)
        for i in range(14):self.w.write(f'file{i}.txt','text')
        self.assertEqual(len(self.w.listing()['files']),12);self.assertEqual(len(self.w.listing(12)['files']),3)

@unittest.skipUnless(sys.platform=='linux' and Path('/usr/bin/bwrap').is_file(),'Real isolated execution requires Pi/Linux Bubblewrap')
class Execution(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='workspace-run-');self.folder=Path(self.tmp.name);self.w=Workspace(self.folder/'artifacts');self.e=Executor(self.w,self.folder/'runs')
    async def asyncTearDown(self):self.tmp.cleanup()
    async def test_actual_runtime_failure_fix_tests_and_playable_game(self):
        r=self.w.write('main.py',GAME);self.w.write('tests/test_game.py',TEST)
        failed=await self.e.run('python_tests','tests');self.assertEqual(failed['status'],'failed');self.assertIn('FAIL',failed['stderr'])
        self.w.write('main.py',GAME.replace('guess == 8','guess == 7'),r['sha256'])
        passed=await self.e.run('python_tests','tests');self.assertEqual(passed['exit_code'],0);self.assertIn('OK',passed['stderr'])
        played=await self.e.run('python','main.py',stdin='7\n');self.assertEqual(played['exit_code'],0);self.assertIn('Correct!',played['stdout'])
        self.assertNotEqual(failed['input_manifest_sha256'],passed['input_manifest_sha256'])
    async def test_skipped_tests_are_failed_execution_and_next_fixed_run_succeeds(self):
        source="""import unittest
class Check(unittest.TestCase):
    @unittest.skip('missing required dependency')
    def test_required(self): pass
    def test_other(self): self.assertEqual(2 + 2, 4)
"""
        created=self.w.write('tests/test_required.py',source)
        skipped=await self.e.run('python_tests','tests')
        self.assertEqual(skipped['status'],'failed',skipped)
        self.assertNotEqual(skipped['exit_code'],0)
        self.assertIn('skipped checks',skipped['stderr'])
        self.w.write('tests/test_required.py',source.replace("    @unittest.skip('missing required dependency')\n",''),created['sha256'])
        fixed=await self.e.run('python_tests','tests')
        self.assertEqual(fixed['status'],'completed',fixed)
        self.assertEqual(fixed['exit_code'],0)
        self.assertNotEqual(skipped['input_manifest_sha256'],fixed['input_manifest_sha256'])
    async def test_private_files_environment_network_and_host_writes_not_accessible(self):
        outside=self.folder/'secret.txt';outside.write_text('HOST_SENTINEL')
        code="""import os,socket
assert 'GOOGLE_API_KEY' not in os.environ
assert not os.path.exists('/home')
assert not os.path.exists('/etc')
assert not os.path.exists(%r)
try:
 open('/workspace/escape.txt','w').write('escape')
 raise AssertionError('workspace writable')
except OSError: pass
s=socket.socket();s.settimeout(.1)
assert s.connect_ex(('192.0.2.1',80)) != 0
print('Isolation checks passed')
"""%str(outside)
        self.w.write('probe.py',code);before=self.w.files();r=await self.e.run('python','probe.py')
        self.assertEqual(r['exit_code'],0,r);self.assertIn('Isolation checks passed',r['stdout']);self.assertEqual(self.w.files(),before);self.assertEqual(outside.read_text(),'HOST_SENTINEL')
    async def test_timeout_output_limits_and_next_command_recovery(self):
        self.w.write('sleep.py','import time; time.sleep(30)');r=await self.e.run('python','sleep.py',timeout_seconds=1);self.assertEqual(r['status'],'timed_out');self.assertLess(r['elapsed_seconds'],2)
        self.w.write('flood.py',"print('x'*50000)");r=await self.e.run('python','flood.py');self.assertEqual(r['status'],'output_limit');self.assertTrue(r['output_truncated']);self.assertLessEqual(len(r['stdout'])+len(r['stderr']),1500)
        self.w.write('fine.py',"print('recovered')");self.assertEqual((await self.e.run('python','fine.py'))['stdout'].strip(),'recovered')
    async def test_cancellation_reaps_and_retains_truthful_receipt(self):
        self.w.write('wait.py','import time; time.sleep(30)');task=asyncio.create_task(self.e.run('python','wait.py',timeout_seconds=3))
        await asyncio.sleep(.2);task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        receipts=list((self.folder/'runs').glob('*/receipt.json'));self.assertEqual(len(receipts),1);r=json.loads(receipts[0].read_text());self.assertEqual(r['status'],'canceled');self.assertIsNotNone(r['exit_code']);self.assertFalse(self.e.busy)
    async def test_catalogue_no_shell_path_or_unbounded_arguments(self):
        for kw in [{'kind':'sh','path':'main.py'},{'kind':'python','path':'../main.py'},{'kind':'python','path':'main.py','timeout_seconds':4},{'kind':'python','path':'main.py','arguments':['x']*9}]:
            with self.subTest(kw=kw),self.assertRaises(WorkspaceError):await self.e.run(**kw)
        self.assertFalse((self.folder/'runs').exists())

class Config(unittest.TestCase):
    def test_explicit_workspace_tools_not_mislabeled_reads(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as d:
            folder=Path(d);c=cfg(folder);self.assertEqual(c['servers'][0]['workspace_tools'],['write_workspace_file','run_workspace_command'])
            p=folder/'config.json';raw=json.loads(p.read_text());raw['servers'][0]['read_tools'].append('write_workspace_file');p.write_text(json.dumps(raw))
            with self.assertRaisesRegex(ValueError,'not_read_tools'):load(p)
            raw['servers'][0]['read_tools'].remove('write_workspace_file');raw['servers'][0]['module']='me3_assistant.utility_server';p.write_text(json.dumps(raw))
            with self.assertRaisesRegex(ValueError,'invalid_workspace_tools'):load(p)

class MCP(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='workspace-mcp-');self.folder=Path(self.tmp.name);self.config=cfg(self.folder);self.events=Events(self.folder/'events.jsonl')
    async def asyncTearDown(self):self.tmp.cleanup()
    async def test_discovery_hides_and_rejects_ungranted_operations_even_direct_rpc(self):
        async with Registry(self.events,self.config) as r:
            self.assertEqual(len(r.declarations()),3)
            with self.assertRaisesRegex(ToolFailure,'authorization_required'):await r.read('workspace__write_workspace_file',1,{'path':'main.py','text':'print(1)'})
            with self.assertRaisesRegex(ToolFailure,'authorization_required'):await r.rpc('workspace','write_workspace_file',1,{'path':'main.py','text':'print(1)'})
            self.assertFalse((self.folder/'artifacts').exists());self.assertEqual(r.calls,0)
            r.session_grant={'workspace_access':True};self.assertEqual(len(r.declarations()),5)
            r.current=lambda g:False
            with self.assertRaisesRegex(ToolFailure,'superseded_before_dispatch'):await r.read('workspace__write_workspace_file',1,{'path':'main.py','text':'print(1)'})
            self.assertEqual(r.calls,0);self.assertFalse((self.folder/'artifacts').exists())
    @unittest.skipUnless(sys.platform=='linux' and Path('/usr/bin/bwrap').is_file(),'Actual MCP cancellation runs on Pi/Linux')
    async def test_real_mcp_execution_cancellation_settles_and_next_read_works(self):
        async with Registry(self.events,self.config) as r:
            r.session_grant={'workspace_access':True}
            await r.read('workspace__write_workspace_file',1,{'path':'wait.py','text':'import time; time.sleep(30)'})
            task=asyncio.create_task(r.read('workspace__run_workspace_command',1,{'command_id':'python','path':'wait.py','timeout_seconds':3}))
            await asyncio.sleep(.3);task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
            await asyncio.wait_for(r.sessions['workspace'][0].cancelled_settled.wait(),2)
            self.assertEqual((await r.read('workspace__list_workspace_files',2,{}))['total_files'],1)
            receipt=json.loads(next((self.folder/'runs').glob('*/receipt.json')).read_text());self.assertEqual(receipt['status'],'canceled');self.assertIsNotNone(receipt['exit_code'])
    @unittest.skipUnless(sys.platform=='linux' and Path('/usr/bin/bwrap').is_file(),'Actual artifact loop runs on Pi/Linux')
    async def test_router_creates_multifile_game_observes_failure_repairs_tests_and_runs_it(self):
        observed=[];test=self
        class Model:
            n=0
            async def decide(self,h,d,c):
                self.n+=1
                if self.n==1:
                    test.assertIn('workspace__write_workspace_file',{x['name'] for x in d});return decision('workspace__write_workspace_file',{'path':'main.py','text':GAME})
                result=c[-1].parts[0].function_response.response['result'];observed.append(result)
                if self.n==2:return decision('workspace__write_workspace_file',{'path':'tests/test_game.py','text':TEST})
                if self.n==3:return decision('workspace__run_workspace_command',{'command_id':'python_tests','path':'tests'})
                if self.n==4:
                    test.assertEqual(result['status'],'failed');test.assertIn('FAIL',result['stderr'])
                    return decision('workspace__write_workspace_file',{'path':'main.py','text':GAME.replace('guess == 8','guess == 7'),'expected_sha256':observed[0]['sha256']})
                if self.n==5:return decision('workspace__run_workspace_command',{'command_id':'python_tests','path':'tests'})
                if self.n==6:
                    test.assertEqual(result['exit_code'],0);return decision('workspace__run_workspace_command',{'command_id':'python','path':'main.py','stdin':'7\n'})
                test.assertEqual(result['exit_code'],0);test.assertIn('Correct!',result['stdout']);return decision(text='Created the game, fixed the observed wrong answer, passed both tests and played the winning path.')
        async with Registry(self.events,self.config) as r:
            r.session_grant={'workspace_access':True};r.budget=FixtureBudget(0);r.journal=Journal(self.folder/'journal.jsonl');r.allowed_actions=[]
            reply=await Router(Model(),r,self.events).answer(history('Create and test a number guessing game with answer seven. Fix any errors and play it.'))
            self.assertIn('passed both tests',reply);self.assertEqual(r.calls,6)
            versions=Workspace(self.folder/'artifacts').listing();self.assertEqual(versions['total_files'],2);self.assertFalse((self.folder/'journal.jsonl').exists())
        self.assertNotIn('def judge',json.dumps(self.events.rows))
    @unittest.skipUnless(sys.platform=='linux' and Path('/usr/bin/bwrap').is_file(),'Actual workflow execution runs on Pi/Linux')
    async def test_failed_workflow_command_stops_later_steps_and_allows_real_repair(self):
        w=Workspace(self.folder/'artifacts');created=w.write('main.py',GAME);w.write('tests/test_game.py',TEST);test=self
        class Model:
            n=0
            async def decide(self,h,d,c):
                self.n+=1
                if self.n==1:return decision('run_goal_steps',{'steps':[{'tool':'workspace__run_workspace_command','arguments':{'command_id':'python_tests','path':'tests'}},{'tool':'workspace__write_workspace_file','arguments':{'path':'must_not_exist.txt','text':'unreachable'}}]})
                result=c[-1].parts[0].function_response.response['result']
                if self.n==2:
                    test.assertEqual(result['status'],'failed');test.assertEqual(result['steps'][0]['status'],'execution_failed');test.assertEqual(result['steps'][1]['status'],'not_run');test.assertIn('FAIL',result['steps'][0]['result']['stderr'])
                    return decision('workspace__write_workspace_file',{'path':'main.py','text':GAME.replace('guess == 8','guess == 7'),'expected_sha256':created['sha256']})
                if self.n==3:return decision('workspace__run_workspace_command',{'command_id':'python_tests','path':'tests'})
                test.assertEqual(result['exit_code'],0);return decision(text='Fixed the failed test. Both checks pass; the later write was not run.')
        async with Registry(self.events,self.config) as r:
            r.session_grant={'workspace_access':True};r.budget=FixtureBudget(0);r.journal=Journal(self.folder/'journal.jsonl');r.allowed_actions=[]
            reply=await Router(Model(),r,self.events).answer(history('Run tests and fix a failure.'))
            self.assertIn('Both checks pass',reply);self.assertEqual(r.calls,3);self.assertFalse((w.root/'must_not_exist.txt').exists())

if __name__=='__main__':unittest.main()
