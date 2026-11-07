"""Actual MCP schema/host grant fixtures; fake runtime, no sockets/providers."""
import copy,importlib,json,shutil,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from mcp.types import CallToolResult,TextContent

PACKET=Path(__file__).resolve().parents[1]
STAGED=(PACKET/'overlay/me3_assistant').is_dir()
ROOT=Path(__import__('os').environ.get('ME3_PROJECT_ROOT',str(Path('/opt/ME3Deployment') if STAGED else PACKET))).resolve()
SOURCES=PACKET/'overlay' if STAGED else ROOT
SCRATCH=PACKET if STAGED else ROOT/'.tmp'
sys.path.insert(0,str(ROOT))
import me3_assistant
if STAGED:me3_assistant.__path__.insert(0,str(SOURCES/'me3_assistant'))
config=importlib.import_module('me3_assistant.config')
registry=importlib.import_module('me3_assistant.registry')
server=importlib.import_module('me3_assistant.workspace_server')
from me3_assistant.workspace_history import manifest

def immediate(coro):
    try:coro.send(None)
    except StopIteration as done:return done.value
    finally:coro.close()
    raise AssertionError('fixture attempted actual asynchronous I/O')

class FakeRuntime:
    def __init__(self):self.calls=[]
    async def dispatch(self,**args):
        self.calls.append(args)
        return dict(ok=True,command=args['command'],status='capabilities',workspace_manifest_sha256='a'*64,network=False,automatic_retry=False,maximum_lifetime_seconds=600,isolated_service_available=False,approved_catalogue_available=False,approved_package_pins=[],services=[],active_environment_id=None)

class Events:
    def emit(self,*a,**k):pass

class NoTimeout:
    async def __aenter__(self):return self
    async def __aexit__(self,*args):return False

class IntegrationChecks(unittest.TestCase):
    def setUp(self):
        self.runtime=FakeRuntime()
        self.app=server.server(PACKET/'authored-unused-workspace',PACKET/'authored-unused-runs',runtime=self.runtime)
        self.tools={t.name:t for t in immediate(self.app.list_tools())}
        self.schema=self.tools['manage_workspace_runtime'].inputSchema
    def arguments(self,request):return dict(request=request,generation=1,request_id='authored-fixture')
    def test_one_real_tool_added_and_schema_valid(self):
        self.assertEqual(set(self.tools),server.READS|server.OPERATIONS)
        self.assertEqual(len(self.tools),8)
        for tool in self.tools.values():Draft202012Validator.check_schema(tool.inputSchema)
        Draft202012Validator(self.schema).validate(self.arguments(dict(command='capabilities')))
    def test_conflicting_fields_host_paths_and_missing_CAS_rejected_before_runtime(self):
        invalid=[dict(command='capabilities',pins=['demo==1']),dict(command='install',pins=['https://example.org/unsafe.whl']),
                 dict(command='status',service_id='../foreign'),dict(command='start',path='/home/owner/private.py',health_check=dict(path='/',contains='OK')),
                 dict(command='restart',service_id='1'*32),dict(command='start',path='main.py',expected_workspace_sha256='a'*64,health_check=dict(path='/',contains='OK'),lifetime_seconds=True),
                 dict(command='stop',service_id='1'*32,host_owner='model-injected')]
        for request in invalid:
            with self.assertRaises(ValidationError):Draft202012Validator(self.schema).validate(self.arguments(request))
        self.assertEqual(self.runtime.calls,[])
    def test_actual_MCP_dispatch_preserves_identity_and_exact_command(self):
        result=immediate(self.app.call_tool('manage_workspace_runtime',self.arguments(dict(command='capabilities'))))
        data=result[1] if isinstance(result,tuple) else json.loads(result[0].text)
        self.assertEqual(self.runtime.calls,[dict(command='capabilities')])
        self.assertEqual((data['generation'],data['request_id'],data['server_role']),(1,'authored-fixture','utility'))
    def test_current_configuration_delta_only_grants_one_owned_operation(self):
        old=json.loads((ROOT/'config/assistant.json').read_bytes());new=json.loads((SOURCES/'config/assistant.json').read_bytes())
        expected=copy.deepcopy(old);tools=next(s for s in expected['servers'] if s['id']=='workspace')['workspace_tools']
        if 'manage_workspace_runtime' not in tools:tools.append('manage_workspace_runtime')
        self.assertEqual(tools.count('manage_workspace_runtime'),1)
        self.assertEqual(new,expected);self.assertEqual((len(new['servers']),len(new['actions'])),(13,12))
        self.assertIn('manage_workspace_runtime',config.WORKSPACE_GRANTED)
    def test_config_loader_refuses_runtime_as_ungranted_read(self):
        SCRATCH.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=SCRATCH,prefix='integration-config-') as folder:
            root=Path(folder);cfg=json.loads((SOURCES/'config/assistant.json').read_bytes())
            for spec in cfg['servers']:
                path=Path(*spec['module'].split('.')).with_suffix('.py');target=root/path;target.parent.mkdir(parents=True,exist_ok=True)
                source=SOURCES/path
                shutil.copyfile(source if source.exists() else ROOT/path,target)
            dest=root/'config/assistant.json';dest.parent.mkdir(parents=True);dest.write_text(json.dumps(cfg))
            self.assertEqual(len(config.load(dest,root)['servers']),13)
            workspace=next(s for s in cfg['servers'] if s['id']=='workspace');workspace['workspace_tools'].remove('manage_workspace_runtime');workspace['read_tools'].append('manage_workspace_runtime');dest.write_text(json.dumps(cfg))
            with self.assertRaisesRegex(ValueError,'workspace_operations_are_not_read_tools'):config.load(dest,root)
    def fixture_registry(self,result,grant=True):
        class Client:
            calls=0
            async def call_tool(self,name,args,**kwargs):
                self.calls+=1
                data={**result,'generation':args['generation'],'request_id':args['request_id'],'server_role':'utility','server_pid':12345}
                return CallToolResult(content=[TextContent(type='text',text=json.dumps(data))])
        r=registry.Registry(Events(),dict(runtime=dict(rpc_calls=5)));client=Client()
        r.sessions={'workspace':(client,dict(module='me3_assistant.workspace_server',identity='me3',role='utility',read_tools=[],workspace_tools=['manage_workspace_runtime']))}
        r.schemas={('workspace','manage_workspace_runtime'):self.tools['manage_workspace_runtime']}
        r.reads={'workspace__manage_workspace_runtime':('workspace','manage_workspace_runtime')}
        r.workspace_operations={'workspace__manage_workspace_runtime'};r.session_grant=dict(workspace_access=grant)
        return r,client
    def test_registry_hides_and_blocks_without_workspace_grant(self):
        r,c=self.fixture_registry({},False);self.assertEqual(r.declarations(),[])
        with self.assertRaisesRegex(registry.ToolFailure,'workspace_authorization_required'):
            immediate(r.read('workspace__manage_workspace_runtime',1,dict(request=dict(command='capabilities'))))
        self.assertEqual(c.calls,0)
    def test_wrong_result_command_rejected_after_actual_identity_checks(self):
        r,c=self.fixture_registry(dict(ok=True,command='stop',status='stopped',service_id='1'*32,outcome_verified=True,owned_namespace_closed=True,automatic_retry=False))
        with patch.object(registry.asyncio,'timeout',return_value=NoTimeout()):
            with self.assertRaisesRegex(registry.ToolFailure,'tool_failed_or_unverified'):
                immediate(r.read('workspace__manage_workspace_runtime',1,dict(request=dict(command='status',service_id='1'*32))))
        self.assertEqual(c.calls,1)
    def test_stale_workspace_CAS_never_reaches_MCP(self):
        r,c=self.fixture_registry({});args=dict(request=dict(command='start',path='main.py',health_check=dict(path='/',contains='OK'),expected_workspace_sha256='a'*64))
        with patch('me3_assistant.workspace.Workspace.files',return_value={'main.py':b'print(1)'}):
            with self.assertRaisesRegex(registry.ToolFailure,'tool_failed_or_unverified'):
                immediate(r.read('workspace__manage_workspace_runtime',1,args))
        self.assertEqual(c.calls,0)
    def test_verified_failed_service_is_reported_as_failed_not_success(self):
        result=dict(ok=False,command='status',status='failed',service_id='1'*32,outcome_verified=True,owned_namespace_closed=True,automatic_retry=False,namespace_local_only=True,host_listening_port=False,expires_at=1,health=dict(healthy=False,error='service_exited'))
        r,c=self.fixture_registry(result)
        with patch.object(registry.asyncio,'timeout',return_value=NoTimeout()):data=immediate(r.read('workspace__manage_workspace_runtime',1,dict(request=dict(command='status',service_id='1'*32))))
        self.assertFalse(data['ok']);self.assertEqual(data['status'],'failed');self.assertEqual(c.calls,1)
    def test_execution_receipt_bound_to_current_verified_environment(self):
        files={'main.py':b'print(1)'};version=manifest(files)
        for returned,accepted in [('a'*64,True),('b'*64,False)]:
            result=dict(ok=True,run_id='2'*32,command_id='python_tests',path='',status='completed',exit_code=0,
                        stdout='authored tests passed',stderr='',output_truncated=False,elapsed_seconds=.01,
                        input_manifest_sha256=version,environment_sha256=returned,
                        isolation='bubblewrap_no_network_readonly_snapshot',host_workspace_modified_by_command=False,
                        content_is_untrusted_data=True)
            r,c=self.fixture_registry(result)
            r.schemas[('workspace','run_workspace_command')]=self.tools['run_workspace_command']
            r.reads['workspace__run_workspace_command']=('workspace','run_workspace_command')
            r.workspace_operations.add('workspace__run_workspace_command')
            with patch('me3_assistant.workspace.Workspace.files',return_value=files),patch('me3_assistant.workspace_runtime.active_environment_sha256',return_value='a'*64),patch.object(registry.asyncio,'timeout',return_value=NoTimeout()):
                call=r.read('workspace__run_workspace_command',1,dict(command_id='python_tests',path=''))
                if accepted:self.assertEqual(immediate(call)['environment_sha256'],'a'*64)
                else:
                    with self.assertRaisesRegex(registry.ToolFailure,'tool_failed_or_unverified'):immediate(call)
            self.assertEqual(c.calls,1)

if __name__=='__main__':unittest.main()
