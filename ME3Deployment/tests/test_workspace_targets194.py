"""Target correction boundaries; LinuxTargets requires real Bubblewrap."""
import asyncio
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

SOURCE = Path(__file__).resolve().parents[1]
ROOT = Path(os.environ.get('ME3_PROJECT_ROOT', '/opt/ME3Deployment')).resolve()
sys.path.insert(0, str(ROOT))
import me3_assistant
me3_assistant.__path__.insert(0, str(SOURCE/'me3_assistant'))
e = importlib.import_module('me3_assistant.workspace_exec')
v = importlib.import_module('me3_assistant.workspace_validation')
from me3_assistant.workspace import Workspace, WorkspaceError
from me3_assistant.workspace_history import validate_execution_result, manifest


def immediate(coro):
    try:
        coro.send(None)
    except StopIteration as done:
        return done.value
    finally:
        coro.close()
    raise AssertionError('unexpected asynchronous work')


class Boundary(unittest.TestCase):
    def test_file_and_directory_targets(self):
        files = {'test_acceptance.py':b'', 'tests/test_one.py':b'', 'suite.py':b''}
        for path in ('test_acceptance.py', 'suite.py', 'tests', ''):
            self.assertIsNone(v.validate_command_targets('python_tests', path, [], files))
        for path in ('missing.py', 'missing', 'test_acceptance.py/other'):
            with self.assertRaises(v.CommandParameterError) as caught:
                v.validate_command_targets('python_tests', path, [], files)
            self.assertEqual(caught.exception.reason, 'command_target_missing')

    def test_missing_targets_and_manifest_stop_before_archive_or_process(self):
        runtime = types.SimpleNamespace(active_packages=lambda p:None, active_environment_sha256=lambda p:None)
        workspace = Mock(root=Path('inert-workspace'))
        workspace.files.return_value = {'app.py':b'pass'}
        executor = e.Executor(workspace, 'inert-runs')
        # Mock run archive access itself: any write/inspection proves a regression.
        executor.runs = Mock()
        with patch.dict(sys.modules, {'me3_assistant.workspace_runtime':runtime}), patch.object(e, 'command', return_value=['inert']) as command, patch.object(asyncio, 'create_subprocess_exec') as spawn:
            for kind, path, args, reason in (
                ('python_tests','missing.py',None,'command_target_missing'),
                ('python','missing.py',None,'command_target_missing'),
                ('python_compile','missing',None,'command_target_missing'),
                ('python_http_check','app.py',['check.json'],'HTTP_check_manifest_missing')):
                with self.assertRaises(v.CommandParameterError) as caught:
                    immediate(executor.run(kind,path,args))
                self.assertEqual(caught.exception.reason, reason)
                result = v.command_rejection(reason)
                self.assertEqual(v.validate_command_rejection(result), reason)
                self.assertFalse(executor.busy)
            self.assertEqual(command.call_count, 4) # isolation argv preflight only
            spawn.assert_not_called()
        self.assertEqual(executor.runs.mock_calls, [])

    def test_storage_security_busy_and_isolation_remain_fatal(self):
        runtime = types.SimpleNamespace(active_packages=lambda p:None, active_environment_sha256=lambda p:None)
        workspace = Mock(root=Path('inert-workspace'))
        workspace.files.side_effect = WorkspaceError('workspace_file_changed')
        executor = e.Executor(workspace, 'inert-runs')
        with patch.dict(sys.modules, {'me3_assistant.workspace_runtime':runtime}), patch.object(e, 'command', return_value=['inert']):
            with self.assertRaises(WorkspaceError) as caught:
                immediate(executor.run('python_tests','test_x.py'))
            self.assertNotIsInstance(caught.exception, v.CommandParameterError)
            self.assertEqual(str(caught.exception), 'workspace_file_changed')
        with patch.dict(sys.modules, {'me3_assistant.workspace_runtime':runtime}), patch.object(e, 'command', side_effect=WorkspaceError('isolated_execution_unavailable')):
            with self.assertRaises(WorkspaceError) as caught:
                immediate(executor.run('python_tests','test_x.py'))
            self.assertNotIsInstance(caught.exception, v.CommandParameterError)
        executor.busy=True
        with self.assertRaises(WorkspaceError) as caught:
            immediate(executor.run('python_tests','test_x.py'))
        self.assertNotIsInstance(caught.exception, v.CommandParameterError)

    def test_forged_or_malformed_feedback_never_accepted(self):
        value = v.command_rejection('command_target_missing')
        for change in ({'execution_started':True}, {'no_effect':1}, {'run_id':'a'*32},
                       {'rejection_reason':'workspace_file_changed'}, {'status':'completed'}):
            with self.assertRaises(WorkspaceError):
                v.validate_command_rejection(dict(value, **change))
        with self.assertRaises(v.CommandParameterError):
            v.validate_command_parameters('python_tests','../private.py')
        with self.assertRaises(v.CommandParameterError):
            v.validate_command_parameters('python_tests','test_x.py',timeout_seconds=4)


class LinuxTargets(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.assertEqual(sys.platform,'linux','Real Linux target qualification required; no skip')
        self.assertTrue(Path('/usr/bin/bwrap').is_file(),'Real Bubblewrap required; no skip')
        scratch=ROOT/'.tmp';scratch.mkdir(exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(prefix='targets194-',dir=scratch)
        self.folder=Path(self.tmp.name).resolve()
        self.workspace=Workspace(self.folder/'workspace')
        self.executor=e.Executor(self.workspace,self.folder/'runs')

    async def asyncTearDown(self):
        if hasattr(self,'tmp'):self.tmp.cleanup()

    async def execute(self,path):
        before=self.workspace.files()
        result=await self.executor.run('python_tests',path,timeout_seconds=3)
        validate_execution_result(result,dict(command_id='python_tests',path=path),manifest(before),None)
        self.assertEqual(self.workspace.files(),before)
        saved=json.loads((self.folder/'runs'/result['run_id']/'receipt.json').read_text())
        self.assertEqual(saved['status'],result['status'])
        return result

    async def test_actual_file_selects_only_that_module(self):
        self.workspace.write('test_acceptance.py','import unittest\nclass Check(unittest.TestCase):\n def test_selected(self): self.assertEqual(7*6,42)\n')
        self.workspace.write('test_other.py','import unittest\nclass Check(unittest.TestCase):\n def test_other(self): self.fail("must not execute")\n')
        result=await self.execute('test_acceptance.py')
        self.assertEqual(result['status'],'completed',result)
        self.assertIn('Ran 1 test',result['stderr'])

    async def test_actual_directory_discovers_all_tests(self):
        for name in ('test_first.py','test_second.py'):
            self.workspace.write('tests/'+name,'import unittest\nclass Check(unittest.TestCase):\n def test_number(self): self.assertEqual(3+4,7)\n')
        result=await self.execute('tests')
        self.assertEqual(result['status'],'completed',result)
        self.assertIn('Ran 2 tests',result['stderr'])

    async def test_actual_failed_test_is_an_execution_receipt(self):
        self.workspace.write('test_failure.py','import unittest\nclass Check(unittest.TestCase):\n def test_failure(self): self.assertEqual(1,2)\n')
        result=await self.execute('test_failure.py')
        self.assertTrue(result['ok'])
        self.assertEqual(result['status'],'failed',result)
        self.assertNotEqual(result['exit_code'],0)
        self.assertIn('FAILED',result['stderr'])

    async def test_actual_missing_target_has_no_archive(self):
        self.workspace.write('app.py','print("not executed")\n')
        with self.assertRaises(v.CommandParameterError) as caught:
            await self.executor.run('python_tests','test_absent.py')
        self.assertEqual(caught.exception.reason,'command_target_missing')
        self.assertFalse((self.folder/'runs').exists())


if __name__=='__main__':unittest.main()
