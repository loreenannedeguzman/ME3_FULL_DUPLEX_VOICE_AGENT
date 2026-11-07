"""Narrow pre-execution feedback through real MCP schema and Registry/Gateway."""
import asyncio,json,sys,tempfile,unittest,uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
from mcp.types import CallToolResult,TextContent
from me3_assistant import registry as registry_module,workspace_server as server_module
from me3_assistant.workspace import WorkspaceError
from me3_assistant.workspace_validation import CommandParameterError,command_rejection
from me3_assistant.workspace_history import manifest
from me3_assistant.actions import Journal
from onit_candidate.gateway import Gateway
from onit_candidate.engine import Engine
from onit_candidate.checks import Events,Budget,FixtureTransport,response,native
from task_mcp.client import ROOT

NAME='workspace__run_workspace_command'
FILES={'main.py':b'print("actual single execution")\n'}
def immediate(coro):
 try:coro.send(None)
 except StopIteration as result:return result.value
 finally:coro.close()
 raise AssertionError('fixture attempted asynchronous IO')
def data_of(result):return result[1] if isinstance(result,tuple) else json.loads(result[0].text)
class NoTimeout:
 async def __aenter__(self):return self
 async def __aexit__(self,*args):return False

class Integration(unittest.TestCase):
 def setUp(self):
  self.app=server_module.server(Path('authored-unused-workspace191'),Path('authored-unused-runs191'))
  self.tool=next(t for t in immediate(self.app.list_tools()) if t.name=='run_workspace_command')
 def fixture(self,result=None,grant=True,identity=True,module='me3_assistant.workspace_server',current=True):
  app=self.app
  class Client:
   calls=0
   async def call_tool(inner,name,args,**kwargs):
    inner.calls+=1
    value=data_of(await app.call_tool(name,args)) if result is None else {**result,'generation':args['generation'] if identity else args['generation']+1,'request_id':args['request_id'],'server_role':'utility','server_pid':123}
    return CallToolResult(content=[TextContent(type='text',text=json.dumps(value))])
  registry=registry_module.Registry(Events(),dict(runtime=dict(rpc_calls=8,batch_calls=3,tool_rounds=6),actions=[]));client=Client()
  registry.sessions={'workspace':(client,dict(module=module,identity='me3',role='utility',workspace_tools=['run_workspace_command'],read_tools=[]))};registry.schemas={('workspace','run_workspace_command'):self.tool};registry.reads={NAME:('workspace','run_workspace_command')};registry.workspace_operations={NAME};registry.session_grant=dict(workspace_access=grant);registry.current=lambda g:current
  return registry,client
 def invoke(self,registry,args):
  with patch('me3_assistant.workspace.Workspace.files',return_value=FILES),patch('me3_assistant.workspace_runtime.active_environment_sha256',return_value=None),patch.object(registry_module.asyncio,'timeout',return_value=NoTimeout()):return immediate(registry.read(NAME,1,args))
 def test_actual_MCP_preflight_response_returns_through_registry(self):
  registry,client=self.fixture()
  with patch.object(server_module.Executor,'run',new_callable=AsyncMock,side_effect=CommandParameterError('invalid_command_timeout')):
   value=self.invoke(registry,dict(command_id='python',path='main.py',timeout_seconds=5))
  self.assertEqual(value,command_rejection('invalid_command_timeout'));self.assertEqual(client.calls,1)
 def test_generic_storage_error_is_not_parameter_feedback(self):
  registry,client=self.fixture()
  with patch.object(server_module.Executor,'run',new_callable=AsyncMock,side_effect=WorkspaceError('workspace_version_changed')):
   with self.assertRaisesRegex(registry_module.ToolFailure,'tool_failed_or_unverified'):self.invoke(registry,dict(command_id='python',path='main.py'))
  self.assertEqual(client.calls,1)
 def test_false_no_effect_or_forged_reason_remains_fatal(self):
  for field,value in [('execution_started',True),('no_effect',1),('rejection_reason','storage_failure'),('guidance','invented')]:
   result=command_rejection('invalid_command_timeout');result[field]=value;registry,_=self.fixture(result)
   with self.assertRaisesRegex(registry_module.ToolFailure,'tool_failed_or_unverified'):self.invoke(registry,dict(command_id='python',path='main.py',timeout_seconds=5))
 def test_wrong_identity_not_relaxed_by_feedback(self):
  registry,client=self.fixture(command_rejection('invalid_command_timeout'),identity=False)
  with self.assertRaisesRegex(registry_module.ToolFailure,'tool_failed_or_unverified'):self.invoke(registry,dict(command_id='python',path='main.py',timeout_seconds=5))
  self.assertEqual(client.calls,1)
 def test_other_module_cannot_use_feedback(self):
  registry,_=self.fixture(command_rejection('invalid_command_timeout'),module='me3_assistant.utility_server')
  with self.assertRaisesRegex(registry_module.ToolFailure,'tool_failed_or_unverified'):self.invoke(registry,dict(command_id='python',path='main.py',timeout_seconds=5))
 def test_ungranted_and_superseded_requests_do_not_dispatch(self):
  for kwargs in [dict(grant=False),dict(current=False)]:
   registry,client=self.fixture(command_rejection('invalid_command_timeout'),**kwargs)
   with self.assertRaises(registry_module.ToolFailure):self.invoke(registry,dict(command_id='python',path='main.py',timeout_seconds=5))
   self.assertEqual(client.calls,0)
 def test_host_schema_still_rejects_unknown_fields_without_dispatch(self):
  registry,client=self.fixture()
  with self.assertRaises(Exception):self.invoke(registry,dict(command_id='python',path='main.py',host_owner='model'))
  self.assertEqual(client.calls,0)
 def test_cancellation_not_converted_to_feedback(self):
  registry,_=self.fixture()
  with patch.object(server_module.Executor,'run',new_callable=AsyncMock,side_effect=asyncio.CancelledError):
   with self.assertRaises(asyncio.CancelledError):self.invoke(registry,dict(command_id='python',path='main.py'))

@unittest.skipUnless(sys.platform=='linux' and Path('/usr/bin/bwrap').is_file(),'Actual original Onit/Bubblewrap child required')
class Native(unittest.IsolatedAsyncioTestCase):
 async def test_model_corrects_refused_arguments_then_one_real_execution(self):
  ROOT.joinpath('.tmp').mkdir(exist_ok=True)
  with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='workspace-feedback191-') as temporary:
   folder=Path(temporary);app=server_module.server(folder/'workspace',folder/'runs');tool=next(t for t in await app.list_tools() if t.name=='run_workspace_command')
   class Client:
    calls=0
    async def call_tool(inner,name,args,**kwargs):
     inner.calls+=1;value=data_of(await app.call_tool(name,args));return CallToolResult(content=[TextContent(type='text',text=json.dumps(value))])
   registry=registry_module.Registry(Events(),dict(runtime=dict(rpc_calls=8,batch_calls=3,tool_rounds=6),actions=[]));client=Client();registry.sessions={'workspace':(client,dict(module='me3_assistant.workspace_server',identity='me3',role='utility',workspace_tools=['run_workspace_command'],read_tools=[]))};registry.schemas={('workspace','run_workspace_command'):tool};registry.reads={NAME:('workspace','run_workspace_command')};registry.workspace_operations={NAME};registry.session_grant=dict(workspace_access=True);registry.journal=Journal(folder/'journal.jsonl');registry.allowed_actions=[];budget=Budget();registry.budget=budget
   first=native(NAME,dict(command_id='python',path='main.py',timeout_seconds=5));second=native(NAME,dict(command_id='python',path='main.py',timeout_seconds=2));second['id']='corrected_workspace_call'
   transport=FixtureTransport(response(calls=[first]),response(calls=[second]),response('The corrected Python command ran once and printed actual single execution.'))
   engine=Engine(ROOT,registry,transport,budget,registry.events,enable_actions=True,ledger_path=folder/'goal-ledger.jsonl')
   with patch('me3_assistant.workspace.Workspace.files',return_value=FILES),patch('me3_assistant.workspace_runtime.active_environment_sha256',return_value=None):result=await engine.answer('Run this authored workspace Python program.',folder/'native-work',folder/'answer',history=[])
   self.assertEqual(result['observations'][0]['result'],command_rejection('invalid_command_timeout'));last=result['observations'][1]['result'];self.assertEqual(last['status'],'completed');self.assertEqual(last['stdout'].strip(),'actual single execution');self.assertEqual((client.calls,budget.used,transport.calls),(2,3,3));self.assertEqual(len(list((folder/'runs').glob('*/receipt.json'))),1);self.assertIsNone(engine.task)
if __name__=='__main__':unittest.main()
