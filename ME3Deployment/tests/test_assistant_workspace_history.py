"""Read-only receipt recovery after real isolated execution/cancellation."""
import asyncio,copy,json,os,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from me3_assistant.workspace import Workspace,WorkspaceError
from me3_assistant.workspace_exec import Executor
from me3_assistant.workspace_history import History,decode,run_id,manifest
from me3_assistant.registry import Registry,ToolFailure,remember_result
from me3_assistant.router import Router
from me3_assistant.actions import Journal
from task_mcp.client import ROOT
from tests.test_assistant_workspace import cfg
from tests.test_assistant_mode import Events,FixtureBudget,decision,history

ID='a'*32
def receipt():
    return {'ok':True,'run_id':ID,'command_id':'python','path':'main.py','status':'completed','exit_code':0,
            'stdout':'one\n','stderr':'','output_truncated':False,'elapsed_seconds':.1,
            'input_manifest_sha256':'b'*64,'isolation':'bubblewrap_no_network_readonly_snapshot',
            'host_workspace_modified_by_command':False,'content_is_untrusted_data':True}

class Validation(unittest.TestCase):
    def test_exact_identity_and_valid_status(self):
        self.assertEqual(decode(json.dumps(receipt()).encode(),ID)['status'],'completed')
        for value in ['../secret','/tmp/x','A'*32,'a'*33,None]:
            with self.subTest(value=value),self.assertRaises(WorkspaceError):run_id(value)
    def test_malformed_or_contradictory_receipt_rejected(self):
        for field,value in [('run_id','c'*32),('status','PASS'),('exit_code',3),('exit_code',False),('stdout','x'*1501),
                            ('elapsed_seconds',float('nan')),('host_workspace_modified_by_command',True),('input_manifest_sha256','old'),('path','../main.py')]:
            data=receipt();data[field]=value
            with self.subTest(field=field,value=value),self.assertRaises(WorkspaceError):decode(json.dumps(data).encode(),ID)
        for raw in [b'{}',b'[]',b'not json',b'{"ok":false,"ok":true}',b' '*8193]:
            with self.subTest(raw=raw[:20]),self.assertRaises(WorkspaceError):decode(raw,ID)
    def test_non_success_outcomes_remain_distinct(self):
        for state,code in [('starting',None),('failed',1),('canceled',-9),('timed_out',-9),('output_limit',-9)]:
            data=receipt();data.update(status=state,exit_code=code)
            self.assertEqual(decode(json.dumps(data).encode(),ID)['status'],state)
    def test_new_command_invalidates_only_cached_run_listing(self):
        tasks=SimpleNamespace(workspace_operations={'workspace__run_workspace_command'})
        key=lambda name:json.dumps([name,{}],sort_keys=True)
        completed={key('workspace__list_workspace_runs'):{'total_runs':0},key('workspace__read_workspace_run'):{'run_id':ID},key('other__get_time'):{'time':1}}
        remember_result(tasks,completed,'workspace__run_workspace_command',{'path':'main.py'},{'ok':True,'run_id':ID})
        self.assertNotIn(key('workspace__list_workspace_runs'),completed)
        self.assertIn(key('workspace__read_workspace_run'),completed);self.assertIn(key('other__get_time'),completed)

def history_config(folder):
    cfg(folder);path=folder/'config.json';data=json.loads(path.read_bytes())
    data['servers'][0]['workspace_tools']+=['list_workspace_runs','read_workspace_run']
    path.write_text(json.dumps(data))
    from me3_assistant.config import load
    return load(path)

class Permission(unittest.IsolatedAsyncioTestCase):
    async def test_discovery_and_direct_rpc_require_workspace_grant(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as tmp:
            folder=Path(tmp);config=history_config(folder);events=Events(folder/'events.jsonl')
            async with Registry(events,config) as registry:
                self.assertEqual(len(registry.declarations()),3)
                for tool,args in [('list_workspace_runs',{}),('read_workspace_run',{'run_id':ID})]:
                    with self.assertRaisesRegex(ToolFailure,'authorization_required'):await registry.rpc('workspace',tool,1,args)
                self.assertEqual(registry.calls,0)
                registry.session_grant={'workspace_access':True}
                self.assertEqual(len(registry.declarations()),7)
                registry.current=lambda g:False
                with self.assertRaisesRegex(ToolFailure,'superseded_before_dispatch'):await registry.read('workspace__list_workspace_runs',1,{})
            self.assertFalse((folder/'runs').exists())
    async def test_receipts_cannot_be_mislabeled_ungranted_reads(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as tmp:
            folder=Path(tmp);history_config(folder);path=folder/'config.json';config=json.loads(path.read_bytes())
            config['servers'][0]['workspace_tools'].remove('read_workspace_run');config['servers'][0]['read_tools'].append('read_workspace_run');path.write_text(json.dumps(config))
            from me3_assistant.config import load
            with self.assertRaisesRegex(ValueError,'not_read_tools'):load(path)

@unittest.skipUnless(sys.platform=='linux' and Path('/usr/bin/bwrap').is_file(),'Receipt execution verification requires actual Pi/Linux sandbox')
class Recovery(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'.tmp');self.folder=Path(self.tmp.name)
        self.workspace=Workspace(self.folder/'artifacts');self.executor=Executor(self.workspace,self.folder/'runs');self.history=History(self.workspace,self.folder/'runs')
    async def asyncTearDown(self):self.tmp.cleanup()
    async def test_completed_receipt_independently_matches_snapshot_and_changed_files(self):
        created=self.workspace.write('main.py',"print('one')")
        result=await self.executor.run('python','main.py');before=len(list((self.folder/'runs').iterdir()))
        recovered=self.history.read(result['run_id']);self.assertEqual(recovered['receipt']['stdout'],'one\n');self.assertTrue(recovered['current_workspace_matches'])
        self.workspace.write('main.py',"print('two')",created['sha256'])
        old=self.history.read(result['run_id']);self.assertFalse(old['current_workspace_matches']);self.assertEqual(old['receipt']['stdout'],'one\n')
        self.assertEqual(len(list((self.folder/'runs').iterdir())),before);self.assertFalse(old['execution_repeated'])
    async def test_canceled_receipt_is_recoverable_without_repeating_execution(self):
        self.workspace.write('main.py','import time;time.sleep(30)');task=asyncio.create_task(self.executor.run('python','main.py',timeout_seconds=3))
        await asyncio.sleep(.2);task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        row=self.history.listing()['runs'][0];result=self.history.read(row['run_id'])
        self.assertEqual(result['receipt']['status'],'canceled');self.assertIsNotNone(result['receipt']['exit_code'])
        self.assertEqual(len(list((self.folder/'runs').iterdir())),1)
    async def test_incomplete_missing_link_and_corrupt_snapshot_fail_closed(self):
        self.assertEqual(self.history.listing()['total_runs'],0);(self.folder/'runs'/ID).mkdir(parents=True)
        value=self.history.read(ID);self.assertFalse(value['receipt_available']);self.assertFalse(value['outcome_verified'])
        with self.assertRaisesRegex(WorkspaceError,'not_found'):self.history.read('b'*32)
        secret=self.folder/'secret';secret.write_text('PRIVATE');(self.folder/'runs'/ID/'receipt.json').symlink_to(secret)
        with self.assertRaises(WorkspaceError):self.history.read(ID)
        self.assertEqual(secret.read_text(),'PRIVATE')
    async def test_saved_input_or_receipt_tampering_rejected(self):
        self.workspace.write('main.py','print(1)');result=await self.executor.run('python','main.py')
        path=self.folder/'runs'/result['run_id'];(path/'input/main.py').write_text('print(2)')
        with self.assertRaisesRegex(WorkspaceError,'snapshot_changed'):self.history.read(result['run_id'])
        (path/'receipt.json').write_text('{}')
        with self.assertRaisesRegex(WorkspaceError,'invalid_execution_receipt'):self.history.read(result['run_id'])
    async def test_bounded_paging_and_no_private_output_in_listing(self):
        self.workspace.write('main.py',"print('PRIVATE_ARTIFACT_OUTPUT')")
        for _ in range(9):await self.executor.run('python','main.py')
        page=self.history.listing();self.assertEqual(len(page['runs']),8);self.assertEqual(page['next_offset'],8)
        self.assertEqual(len(self.history.listing(8)['runs']),1);self.assertNotIn('PRIVATE_ARTIFACT_OUTPUT',json.dumps(page))
        with self.assertRaises(WorkspaceError):self.history.listing(True)
    async def test_new_mcp_connection_recovers_prior_failure_without_command(self):
        self.workspace.write('main.py','raise ValueError("public failure")');run=await self.executor.run('python','main.py')
        config=history_config(self.folder);events=Events(self.folder/'events.jsonl');test=self
        class Model:
            n=0
            async def decide(self,h,d,c):
                self.n+=1
                if self.n==1:return decision('workspace__list_workspace_runs',{})
                result=c[-1].parts[0].function_response.response['result']
                if self.n==2:
                    test.assertEqual(result['runs'][0]['status'],'failed')
                    return decision('workspace__read_workspace_run',{'run_id':result['runs'][0]['run_id']})
                test.assertEqual(result['receipt']['exit_code'],1);test.assertFalse(result['execution_repeated'])
                return decision(text='The earlier run failed with exit code 1. It was inspected without rerunning it.')
        async with Registry(events,config) as registry:
            registry.session_grant={'workspace_access':True};registry.budget=FixtureBudget(0);registry.journal=Journal(self.folder/'journal.jsonl');registry.allowed_actions=[]
            reply=await Router(Model(),registry,events).answer(history('Check the earlier artifact run; do not rerun it.'))
            self.assertIn('without rerunning',reply);self.assertEqual(registry.calls,2)
        self.assertEqual(len(list((self.folder/'runs').iterdir())),1)

if __name__=='__main__':unittest.main()
