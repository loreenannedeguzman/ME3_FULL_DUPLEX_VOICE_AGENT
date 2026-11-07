"""Project unittest profile, real compact diagnostics and finite isolation bounds."""
import asyncio,contextlib,hashlib,io,json,os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from me3_assistant import workspace_exec as e,workspace_validation as v,workspace_history as h,workspace_server as s
from me3_assistant.workspace import Workspace,WorkspaceError

def immediate(coro):
    try:coro.send(None)
    except StopIteration as result:return result.value
    finally:coro.close()
    raise AssertionError('unexpected IO')
def summary(suite):
    output=io.StringIO()
    with patch.object(sys,'argv',['fixture','python_project_tests','/workspace']),patch.object(unittest.defaultTestLoader,'discover',return_value=suite),contextlib.redirect_stdout(output):
        with unittest.TestCase().assertRaises(SystemExit) as caught:exec('import os,sys,unittest\n'+e.PROJECT_TEST_BOOTSTRAP,{})
    raw=output.getvalue();return json.loads(raw),caught.exception.code,raw

class Pure(unittest.TestCase):
    def test_profile_preflight_bounds_targets_and_legacy_timeout(self):
        self.assertEqual(v.validate_command_parameters('python_project_tests','',timeout_seconds=20),[])
        v.validate_command_targets('python_project_tests','test_app.py',[],{'test_app.py':b'pass'})
        for options in (dict(timeout_seconds=21),dict(timeout_seconds=0),dict(arguments=['unapproved'])):
            with self.assertRaises(v.CommandParameterError):v.validate_command_parameters('python_project_tests','',**options)
        with self.assertRaises(v.CommandParameterError):v.validate_command_parameters('python_tests','',timeout_seconds=4)
        with self.assertRaises(v.CommandParameterError):v.validate_command_targets('python_project_tests','missing.py',[],{'test_app.py':b'pass'})
    def test_actual_command_assembly_new_resource_limits_and_unchanged_legacy(self):
        with patch.object(e.sys,'platform','linux'),patch.object(e.Path,'is_file',return_value=True),patch.object(e.Path,'exists',return_value=True):
            new=e.command(Path('/snapshot'),'python_project_tests','test_app.py',[]);old=e.command(Path('/snapshot'),'python_tests','test_app.py',[])
        code=new[new.index('-c')+1];oldcode=old[old.index('-c')+1]
        for text in ('(8,8)','(536870912,536870912)','(16777216,16777216)'):self.assertIn(text,code)
        self.assertEqual(new[new.index('--size')+1],'33554432');self.assertEqual(old[old.index('--size')+1],'8388608');self.assertEqual(oldcode,e.BOOTSTRAP)
        for text in ('(2,2)','(268435456,268435456)','(1048576,1048576)'):self.assertIn(text,oldcode)
        for flag in ('--unshare-all','--clearenv','--die-with-parent','--cap-drop','--remount-ro'):self.assertIn(flag,new)
    def test_compact_real_counts_errors_skips_output_and_no_false_success(self):
        class Cases(unittest.TestCase):
            def test_pass(self):print('x'*6000)
            def test_fail(self):self.fail('assertion diagnostic')
            def test_error(self):raise OSError(27,'File too large fixture')
            @unittest.skip('authored skip')
            def test_skip(self):pass
        value,exit_code,raw=summary(unittest.defaultTestLoader.loadTestsFromTestCase(Cases))
        self.assertEqual((value['tests_run'],value['successes'],value['failure_events'],value['error_events'],value['skipped']),(4,1,1,1,1));self.assertEqual(exit_code,1);self.assertFalse(value['success']);self.assertTrue(value['complete']);self.assertGreater(value['captured_output_bytes'],6000);self.assertLessEqual(len(raw.encode()),1401)
        self.assertIn('File too large fixture',raw);self.assertIn('OSError',raw)
    def test_many_unicode_errors_stay_bounded_and_counts_remain_real(self):
        class Cases(unittest.TestCase):
            def runTest(self):raise ValueError('😀'*2000)
        value,exit_code,raw=summary(unittest.TestSuite(Cases() for _ in range(12)))
        self.assertEqual((value['tests_run'],value['error_events'],value['successes']),(12,12,0));self.assertEqual(exit_code,1);self.assertTrue(value['diagnostics_truncated']);self.assertLessEqual(len(raw.encode()),1401)
    def test_success_and_empty_suite_are_distinct(self):
        class Case(unittest.TestCase):
            def runTest(self):pass
        value,code,_=summary(unittest.TestSuite([Case()]));self.assertEqual(code,0);self.assertTrue(value['success']);self.assertEqual(value['successes'],1)
        value,code,_=summary(unittest.TestSuite());self.assertEqual(code,1);self.assertFalse(value['success']);self.assertEqual(value['tests_run'],0)
    def test_actual_MCP_catalogue_schema_and_receipt_contract_admit_new_command(self):
        app=s.server(Path('unused235-workspace'),Path('unused235-runs'));tools=immediate(app.list_tools());tool=next(t for t in tools if t.name=='run_workspace_command')
        self.assertIn('python_project_tests',tool.inputSchema['properties']['command_id']['enum']);self.assertIn('512MiB',tool.description)
        _,_,raw=summary(unittest.TestSuite([unittest.FunctionTestCase(lambda:None)]))
        rid='a'*32;receipt=dict(ok=True,run_id=rid,command_id='python_project_tests',path='',status='completed',exit_code=0,stdout=raw,stderr='',output_truncated=False,elapsed_seconds=.1,input_manifest_sha256='b'*64,isolation='bubblewrap_no_network_readonly_snapshot',host_workspace_modified_by_command=False,content_is_untrusted_data=True)
        self.assertEqual(h.decode(json.dumps(receipt).encode(),rid)['command_id'],'python_project_tests')
        self.assertRaises(WorkspaceError,h.validate_execution_result,receipt,dict(command_id='python_tests',path=''),'b'*64)
        for bad in ('{}','',json.dumps({**json.loads(raw),'tests_run':0}),json.dumps({**json.loads(raw),'skipped':1}),json.dumps({**json.loads(raw),'complete':False})):
            self.assertRaises(WorkspaceError,h.decode,json.dumps({**receipt,'stdout':bad}).encode(),rid)

@unittest.skipUnless(sys.platform=='linux' and Path('/usr/bin/bwrap').is_file(),'actual Linux Bubblewrap required')
class Actual(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='me3-tests235-',dir=Path.home());self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name);self.workspace=Workspace(self.root/'workspace');self.executor=e.Executor(self.workspace,self.root/'runs')
    async def execute(self,source,timeout=20):
        self.workspace.write('test_app.py',source);before=self.workspace.files();result=await self.executor.run('python_project_tests','test_app.py',timeout_seconds=timeout)
        self.assertEqual(self.workspace.files(),before);self.assertFalse(result['host_workspace_modified_by_command']);h.validate_execution_result(result,dict(command_id='python_project_tests',path='test_app.py'),h.manifest(before));return result
    async def test_real_resource_profile_10MB_fixture_and_snapshot_network_bounds(self):
        result=await self.execute('''import os,resource,socket,tempfile,unittest
class Checks(unittest.TestCase):
 def test_resources(self):
  self.assertEqual(resource.getrlimit(resource.RLIMIT_CPU),(8,8))
  self.assertEqual(resource.getrlimit(resource.RLIMIT_AS),(536870912,536870912))
  self.assertEqual(resource.getrlimit(resource.RLIMIT_FSIZE),(16777216,16777216))
  with tempfile.TemporaryFile() as f:f.truncate(10000001);self.assertEqual(os.fstat(f.fileno()).st_size,10000001)
  stat=os.statvfs('/tmp');self.assertEqual(stat.f_blocks*stat.f_frsize,33554432)
 def test_isolation(self):
  with self.assertRaises(OSError):open('/workspace/host_write.py','w')
  with socket.socket() as sock:
   sock.settimeout(.2)
   with self.assertRaises(OSError):sock.connect(('1.1.1.1',80))
''')
        self.assertEqual(result['status'],'completed');value=json.loads(result['stdout']);self.assertEqual((value['tests_run'],value['successes'],value['skipped']),(2,2,0));self.assertTrue(value['success']);self.assertFalse(result['output_truncated'])
    async def test_real_failure_summary_survives_noisy_test_output(self):
        result=await self.execute('''import unittest
class Checks(unittest.TestCase):
 def test_a(self):print('noise'*5000)
 def test_b(self):raise ValueError('actual bounded diagnostic')
 def test_c(self):self.fail('actual assertion diagnostic')
''')
        self.assertEqual(result['status'],'failed');self.assertEqual(result['exit_code'],1);self.assertFalse(result['output_truncated']);value=json.loads(result['stdout']);self.assertEqual((value['tests_run'],value['successes'],value['error_events'],value['failure_events']),(3,1,1,1));self.assertIn('actual bounded diagnostic',result['stdout']);self.assertLessEqual(len(result['stdout'].encode())+len(result['stderr'].encode()),1500)
    async def test_real_wall_timeout_and_cleanup_are_still_verified(self):
        result=await self.execute('''import time,unittest
class Checks(unittest.TestCase):
 def test_wait(self):time.sleep(10)
''',timeout=1)
        self.assertEqual(result['status'],'timed_out');self.assertIsInstance(result['exit_code'],int);self.assertLess(result['exit_code'],0);self.assertFalse(self.executor.busy)

if __name__=='__main__':unittest.main(verbosity=2)
