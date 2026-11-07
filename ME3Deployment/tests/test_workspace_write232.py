"""Bounded reusable artifact writes and independently validated no-effect feedback.

No owner files, credentials, provider or external process calls.
"""
import json,os,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from mcp.types import CallToolResult,TextContent
from me3_assistant import workspace as w,workspace_server as s,registry as r
NAME='workspace__write_workspace_file'

def immediate(coro):
    try:coro.send(None)
    except StopIteration as done:return done.value
    finally:coro.close()
    raise AssertionError('fixture attempted IO')
def data_of(value):return value[1] if isinstance(value,tuple) else json.loads(value[0].text)
class NoTimeout:
    async def __aenter__(self):return self
    async def __aexit__(self,*args):return False
class Events:
    def __init__(self):self.rows=[]
    def emit(self,name,**kw):self.rows.append((name,kw))

class Pure(unittest.TestCase):
    def test_exact_ASCII_and_multibyte_bounds_and_invalid_encoding(self):
        self.assertEqual((w.MAX_WRITE,w.MAX_FILE,w.MAX_TOTAL,w.MAX_FILES),(65536,65536,1048576,128))
        for text in ('x'*65536,'😀'*16384,'\x01'*65536):self.assertEqual(len(w.validate_write_parameters('app/main.py',text)[1]),65536)
        for text in ('x'*65537,'😀'*16385,'bad\x00text','bad\ud800',None):
            with self.assertRaisesRegex(w.WriteParameterError,'workspace_write_size_or_encoding'):w.validate_write_parameters('app/main.py',text)

    def test_only_pure_preflight_can_return_no_effect_before_any_filesystem(self):
        workspace=w.Workspace(Path('unused232'))
        cases=[('../x.py','ok','NEW','invalid_workspace_path'),('x.exe','ok','NEW','unsupported_workspace_extension'),('x.py','x'*65537,'NEW','workspace_write_size_or_encoding'),('x.py','ok','old','invalid_workspace_version')]
        with patch.object(workspace,'folder',side_effect=AssertionError('preflight must not touch filesystem')):
            for path,text,version,reason in cases:
                with self.assertRaises(w.WriteParameterError) as caught:workspace.write(path,text,version)
                self.assertEqual(caught.exception.reason,reason)
                self.assertEqual(w.validate_write_rejection(w.write_rejection(reason)),reason)

    def test_feedback_exact_shape_types_and_known_reason_required(self):
        base=w.write_rejection('invalid_workspace_path')
        for field,value in [('no_effect',1),('execution_started',True),('automatic_retry',True),('rejection_reason','workspace_version_changed'),('guidance','invented'),('extra',True)]:
            invalid={**base,field:value};self.assertRaises(w.WorkspaceError,w.validate_write_rejection,invalid)
        self.assertRaises(w.WorkspaceError,w.write_rejection,'workspace_busy')

class Contract(unittest.TestCase):
    def setUp(self):
        self.app=s.server(Path('unused232-workspace'),Path('unused232-runs'))
        self.tools={tool.name:tool for tool in immediate(self.app.list_tools())}
    def fixture(self,result=None,grant=True,current=True,identity=True,module='me3_assistant.workspace_server',alias=NAME):
        app=self.app
        class Client:
            calls=0
            async def call_tool(inner,name,args,**kwargs):
                inner.calls+=1
                value=data_of(await app.call_tool(name,args)) if result is None else {**result,'generation':args['generation'] if identity else args['generation']+1,'request_id':args['request_id'],'server_role':'utility','server_pid':123}
                return CallToolResult(content=[TextContent(type='text',text=json.dumps(value))])
        registry=r.Registry(Events(),dict(runtime=dict(rpc_calls=16,batch_calls=3,tool_rounds=24),actions=[]));client=Client();server,tool=alias.split('__',1)
        registry.sessions={server:(client,dict(module=module,identity='me3',role='utility',workspace_tools=[tool],read_tools=[]))};registry.schemas={(server,tool):self.tools['write_workspace_file']};registry.reads={alias:(server,tool)};registry.workspace_operations={alias};registry.session_grant=dict(workspace_access=grant);registry.current=lambda g:current
        return registry,client
    def invoke(self,registry,args,alias=NAME):
        with patch.object(r.asyncio,'timeout',return_value=NoTimeout()):return immediate(registry.read(alias,1,args))
    def test_actual_MCP_schema_capabilities_and_preflight_round_trip(self):
        caps=data_of(immediate(self.app.call_tool('workspace_capabilities',dict(generation=1,request_id='fixture232'))))
        self.assertEqual((caps['max_file_write_bytes'],caps['max_file_bytes'],caps['max_workspace_bytes'],caps['max_workspace_files']),(65536,65536,1048576,128))
        self.assertIn('65536',self.tools['write_workspace_file'].description)
        registry,client=self.fixture();value=self.invoke(registry,dict(path='../escape.py',text='synthetic'))
        self.assertEqual(value,w.write_rejection('invalid_workspace_path'));self.assertEqual(client.calls,1);self.assertEqual(registry.calls,1)
    def test_forged_feedback_and_wrong_identity_stay_fatal(self):
        base=w.write_rejection('invalid_workspace_path')
        for result,identity in [({**base,'no_effect':1},True),({**base,'guidance':'untrusted'},True),({**base,'rejection_reason':'workspace_busy'},True),(base,False)]:
            registry,_=self.fixture(result,identity=identity)
            with self.assertRaisesRegex(r.ToolFailure,'tool_failed_or_unverified'):self.invoke(registry,dict(path='../escape.py',text='ok'))
    def test_ungranted_superseded_and_other_module_do_not_gain_feedback(self):
        for options in (dict(grant=False),dict(current=False)):
            registry,client=self.fixture(w.write_rejection('invalid_workspace_path'),**options)
            with self.assertRaises(r.ToolFailure):self.invoke(registry,dict(path='../escape.py',text='ok'))
            self.assertEqual(client.calls,0)
        registry,_=self.fixture(w.write_rejection('invalid_workspace_path'),module='me3_assistant.utility_server')
        with self.assertRaises(r.ToolFailure):self.invoke(registry,dict(path='../escape.py',text='ok'))
    def test_actual_storage_and_CAS_errors_never_turn_into_feedback(self):
        for reason in ('workspace_busy','workspace_capacity_limit','workspace_file_already_exists','workspace_version_changed','workspace_write_unverified','unknown_failure'):
            registry,_=self.fixture()
            with patch.object(s.Workspace,'write',side_effect=w.WorkspaceError(reason)):
                with self.assertRaisesRegex(r.ToolFailure,'tool_failed_or_unverified'):self.invoke(registry,dict(path='main.py',text='ok'))
    def test_large_serialized_arguments_only_for_attested_owned_write_and_results_still_small(self):
        args=dict(path='controls.txt',text='\x01'*65536)
        self.assertGreater(len(json.dumps(args)),393000);self.assertLess(len(json.dumps(args)),400000)
        registry,client=self.fixture(dict(ok=True,verified_by_fresh_read=True))
        self.assertTrue(self.invoke(registry,args)['ok']);self.assertEqual(client.calls,1)
        for options in (dict(module='me3_assistant.utility_server'),dict(alias='other__write_workspace_file')):
            registry,client=self.fixture(dict(ok=True),**options)
            with self.assertRaises(r.ToolFailure):self.invoke(registry,args,options.get('alias',NAME))
            self.assertEqual(client.calls,0)
        registry,_=self.fixture(dict(ok=True,padding='x'*12000))
        with self.assertRaisesRegex(r.ToolFailure,'tool_failed_or_unverified'):self.invoke(registry,dict(path='main.py',text='ok'))

@unittest.skipUnless(os.name=='posix','actual POSIX writes required')
class RealMCP(unittest.TestCase):
    fixture=Contract.fixture
    invoke=Contract.invoke
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='me3-write232-',dir=Path.home());self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)/'artifacts'
        self.app=s.server(self.root,Path(self.temp.name)/'runs');self.tools={tool.name:tool for tool in immediate(self.app.list_tools())}
    def test_real_MCP_64KiB_ASCII_Unicode_and_escaped_text_readback(self):
        for path,text in [('ascii.txt','x'*65536),('unicode.txt','😀'*16384),('escaped.txt','\x01'*65536)]:
            registry,client=self.fixture();value=self.invoke(registry,dict(path=path,text=text))
            self.assertTrue(value['verified_by_fresh_read']);self.assertEqual(value['bytes'],65536)
            raw=(self.root/path).read_bytes();self.assertEqual(raw,text.encode());self.assertEqual(value['sha256'],w.digest(raw));self.assertEqual(client.calls,1)
        self.assertEqual(len(w.Workspace(self.root).files()),3)
    def test_real_preflight_leaves_existing_and_absent_workspace_unchanged(self):
        workspace=w.Workspace(self.root);registry,_=self.fixture()
        value=self.invoke(registry,dict(path='bad.exe',text='x'));self.assertTrue(value['no_effect']);self.assertFalse(self.root.exists())
        first=workspace.write('existing.py','original\n');before=workspace.files()
        value=self.invoke(registry,dict(path='nested/new.py',text='x'*65537));self.assertTrue(value['no_effect']);self.assertEqual(workspace.files(),before);self.assertFalse((self.root/'nested').exists())
        with self.assertRaises(w.WorkspaceError):workspace.write('existing.py','wrong','a'*64)
        self.assertEqual(workspace.files(),before)
        second=workspace.write('existing.py','replacement\n',first['sha256']);self.assertEqual(second['previous_sha256'],first['sha256']);self.assertEqual((self.root/'.versions'/first['sha256']).read_bytes(),b'original\n')
    def test_real_symlink_and_total_file_limits_remain_fatal(self):
        workspace=w.Workspace(self.root);workspace.write('first.py','original\n');target=Path(self.temp.name)/'outside.py';target.write_text('outside\n');(self.root/'link.py').symlink_to(target)
        registry,_=self.fixture()
        with self.assertRaises(r.ToolFailure):self.invoke(registry,dict(path='new.py',text='new\n'))
        self.assertEqual(target.read_text(),'outside\n');(self.root/'link.py').unlink()
        with patch.object(w,'MAX_TOTAL',8):
            with self.assertRaisesRegex(w.WorkspaceError,'workspace_capacity_limit'):workspace.write('new.py','too large\n')
        with patch.object(w,'MAX_FILES',1):
            with self.assertRaises(w.WorkspaceError):workspace.write('new.py','x')
        self.assertFalse((self.root/'new.py').exists())

class CachePure(unittest.TestCase):
    def test_reserved_cache_component_denied_for_file_and_directory_paths(self):
        for path in ('__pycache__','__pycache__/main.py','app/__pycache__/main.py'):
            for directory in (False,True):
                with self.assertRaisesRegex(w.WorkspaceError,'invalid_workspace_path'):w.relative(path,directory=directory)
        self.assertEqual(w.relative('app/cache_main.py'),['app','cache_main.py'])
        self.assertEqual(w.relative('__pycache__extra/main.py'),['__pycache__extra','main.py'])
    def test_direct_cache_read_write_never_access_filesystem(self):
        workspace=w.Workspace(Path('unused238'))
        with patch.object(workspace,'folder',side_effect=AssertionError('reserved path must not access filesystem')):
            with self.assertRaises(w.WorkspaceError):workspace.read('app/__pycache__/main.py')
            with self.assertRaises(w.WriteParameterError) as caught:workspace.write('app/__pycache__/main.py','never hidden')
            self.assertEqual(caught.exception.reason,'invalid_workspace_path')

@unittest.skipUnless(os.name=='posix','actual POSIX cache exclusion required')
class CachePOSIX(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='me3-cache238-',dir=Path.home());self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.workspace=w.Workspace(self.base/'workspace');self.original=self.workspace.write('app/main.py','VALUE=42\n')
    def test_actual_generated_pyc_excluded_without_modification_or_manifest_change(self):
        import py_compile
        from me3_assistant.workspace_history import manifest
        before=self.workspace.files();before_manifest=manifest(before);source=self.workspace.root/'app/main.py';caches=[]
        for directory in (self.workspace.root/'__pycache__',source.parent/'__pycache__'):
            directory.mkdir();target=directory/('main.'+sys.implementation.cache_tag+'.pyc');py_compile.compile(str(source),cfile=str(target),doraise=True);caches.append((target,target.read_bytes()))
        self.assertEqual(self.workspace.files(),before);self.assertEqual(manifest(self.workspace.files()),before_manifest)
        self.assertEqual(self.workspace.listing()['total_files'],1)
        self.assertEqual(self.workspace.read('app/main.py')['sha256'],self.original['sha256'])
        for path,raw in caches:self.assertEqual(path.read_bytes(),raw)
    def test_cache_symlink_regular_file_and_special_entry_are_rejected(self):
        cache=self.workspace.root/'__pycache__';outside=self.base/'outside';outside.mkdir();target=outside/'private.txt';target.write_text('unrelated synthetic fixture')
        cache.symlink_to(outside,target_is_directory=True)
        try:self.assertRaisesRegex(w.WorkspaceError,'workspace_cache_not_directory',self.workspace.files)
        finally:cache.unlink()
        cache.write_bytes(b'not a directory')
        try:self.assertRaisesRegex(w.WorkspaceError,'workspace_cache_not_directory',self.workspace.files)
        finally:cache.unlink()
        os.mkfifo(cache)
        try:self.assertRaisesRegex(w.WorkspaceError,'workspace_cache_not_directory',self.workspace.files)
        finally:cache.unlink()
        self.assertEqual(target.read_text(),'unrelated synthetic fixture');self.assertEqual(self.workspace.read('app/main.py')['sha256'],self.original['sha256'])
    def test_other_unsupported_files_remain_fatal_and_source_CAS_still_applies(self):
        root=self.workspace.root;cache=root/'app/__pycache__';cache.mkdir();cached=cache/'main.cpython-fixture.pyc';cached.write_bytes(b'cache bytes retained')
        bad=root/'loose.pyc';bad.write_bytes(b'not an accepted artifact')
        try:self.assertRaisesRegex(w.WorkspaceError,'unsupported_workspace_extension',self.workspace.files)
        finally:bad.unlink()
        with self.assertRaisesRegex(w.WorkspaceError,'workspace_version_changed'):self.workspace.write('app/main.py','wrong\n','a'*64)
        self.assertEqual(self.workspace.read('app/main.py')['sha256'],self.original['sha256'])
        changed=self.workspace.write('app/main.py','VALUE=43\n',self.original['sha256']);self.assertNotEqual(changed['sha256'],self.original['sha256'])
        self.assertEqual((root/'.versions'/self.original['sha256']).read_bytes(),b'VALUE=42\n');self.assertEqual(cached.read_bytes(),b'cache bytes retained')

if __name__=='__main__':unittest.main(verbosity=2)
