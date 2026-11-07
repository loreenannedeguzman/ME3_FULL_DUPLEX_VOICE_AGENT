"""Pure runtime boundary checks plus explicitly Linux-only real jail checks."""
import asyncio, copy, importlib.util, json, os, socket, stat, sys, tempfile, unittest, zipfile
from pathlib import Path
from unittest.mock import patch,AsyncMock
from me3_assistant.workspace import Workspace,WorkspaceError,digest
from me3_assistant.workspace_runtime import Runtime,active_packages,files_manifest,health,validate_runtime_result,wheel_contents

def make_wheel(root,name='demo_pkg',version='1.0',extra=None,requires=()):
 filename=name+'-'+version+'-py3-none-any.whl';path=root/filename;files={name+'/__init__.py':b'VALUE = 42\n',name+'-'+version+'.dist-info/METADATA':('Metadata-Version: 2.1\nName: '+name+'\nVersion: '+version+'\n'+''.join('Requires-Dist: '+v+'\n' for v in requires)+'\n').encode(),name+'-'+version+'.dist-info/WHEEL':b'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n'}
 files[name+'-'+version+'.dist-info/RECORD']=b'';files.update(extra or {})
 with zipfile.ZipFile(path,'w') as archive:
  for key,raw in files.items():archive.writestr(key,raw)
 return dict(filename=filename,name=name,version=version,sha256=digest(path.read_bytes()))

class Boundary(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve();self.wheels=self.root/'wheels';self.wheels.mkdir(mode=0o700);self.runtime=Runtime(Workspace(self.root/'workspace'),self.root/'runtime',self.wheels)
 def tearDown(self):self.tmp.cleanup()
 def catalogue(self,entries):
  (self.wheels/'approved.json').write_text(json.dumps(dict(version=1,wheels=entries)))
 def test_approved_install_fresh_hash_and_active_environment(self):
  self.catalogue([make_wheel(self.wheels)]);result=self.runtime.install(['demo_pkg==1.0']);self.assertEqual(result['status'],'installed_verified');self.assertFalse(result['imports_verified']);site=active_packages(self.runtime.root);self.assertEqual((site/'demo_pkg/__init__.py').read_bytes(),b'VALUE = 42\n')
 def test_changed_installed_bytes_not_imported(self):
  self.catalogue([make_wheel(self.wheels)]);result=self.runtime.install(['demo_pkg==1.0']);site=self.runtime.environment(result['environment_id']);(site/'demo_pkg/__init__.py').write_text('VALUE=99')
  with self.assertRaisesRegex(WorkspaceError,'installed_environment_changed'):active_packages(self.runtime.root)
 def test_download_url_and_loose_version_refused(self):
  for pin in ['https://example.test/pkg.whl','demo_pkg>=1','demo_pkg','-r requirements.txt']:
   with self.assertRaisesRegex(WorkspaceError,'exact_bounded_package_pins'):self.runtime.install([pin])
 def test_unknown_package_does_not_create_environment(self):
  self.catalogue([])
  with self.assertRaisesRegex(WorkspaceError,'dependency_not_in_approved'):self.runtime.install(['missing==1'])
  self.assertFalse(self.runtime.root.exists())
 def test_missing_catalogue_is_honest(self):
  value=self.runtime.capabilities();self.assertFalse(value['approved_catalogue_available']);self.assertEqual(value['approved_package_pins'],[])
 def test_unapproved_wheel_hash_refused(self):
  entry=make_wheel(self.wheels);self.catalogue([entry]);(self.wheels/entry['filename']).write_bytes(b'changed')
  with self.assertRaisesRegex(WorkspaceError,'approved_wheel_hash_mismatch'):self.runtime.install(['demo_pkg==1.0'])
 def test_wheel_path_traversal_refused(self):
  entry=make_wheel(self.wheels,extra={'../escaped.py':b'x=1'})
  with self.assertRaisesRegex(WorkspaceError,'wheel_relative_path'):wheel_contents(self.wheels/entry['filename'],entry)
 def test_pth_native_and_script_wheels_refused(self):
  for key in ['evil.pth','demo_pkg/lib.so','demo_pkg-1.0.data/scripts/evil']:
   entry=make_wheel(self.wheels,extra={key:b'x'})
   with self.assertRaisesRegex(WorkspaceError,'wheel_executable_hook'):wheel_contents(self.wheels/entry['filename'],entry)
 def test_exact_approved_transitive_dependency_installed(self):
  first=make_wheel(self.wheels,requires=['helper_pkg==2.0']);second=make_wheel(self.wheels,'helper_pkg','2.0');self.catalogue([first,second]);result=self.runtime.install(['demo_pkg==1.0']);self.assertEqual(result['packages'],['demo-pkg==1.0','helper-pkg==2.0'])
 def test_unsupported_transitive_requirement_is_honest(self):
  entry=make_wheel(self.wheels,requires=['helper_pkg>=2.0']);self.catalogue([entry])
  with self.assertRaisesRegex(WorkspaceError,'only_exact_approved_transitive'):self.runtime.install(['demo_pkg==1.0'])
 def test_health_never_accepts_external_URL_or_POST(self):
  for check in [dict(path='https://example.test',status=200,contains='x'),dict(path='//example.test',status=200,contains='x'),dict(path='/',status=200,contains='x',method='POST')]:
   with self.assertRaises(WorkspaceError):health(check)
 def test_service_receipt_requires_health_proof(self):
  result=dict(ok=True,command='start',service_id='a'*32,path='main.py',status='running',workspace_manifest_sha256='b'*64,health=dict(healthy=False),outcome_verified=True,namespace_local_only=True,host_listening_port=False,automatic_retry=False)
  with self.assertRaisesRegex(WorkspaceError,'runtime_health_proof'):validate_runtime_result(result,dict(command='start',path='main.py',expected_workspace_sha256='b'*64))
 def test_status_cannot_substitute_another_service(self):
  with self.assertRaisesRegex(WorkspaceError,'runtime_service_identity'):validate_runtime_result(dict(ok=False,command='status',service_id='a'*32,status='unverified',automatic_retry=False),dict(command='status',service_id='b'*32))
 def test_laptop_process_or_path_not_a_service_ID(self):
  for identifier in ['1234','../../other','/home/user/app','chrome.exe']:
   with self.assertRaises(WorkspaceError):self.runtime.service_dir(identifier)
 def test_exact_dispatch_fields_no_generic_shell(self):
  with self.assertRaisesRegex(WorkspaceError,'runtime_command_field'):asyncio.run(self.runtime.dispatch('capabilities',shell='rm -rf /'))
 def test_stale_workspace_start_rejected_before_process_creation(self):
  with patch.object(self.runtime,'available',return_value=True),patch.object(self.runtime.workspace,'files',return_value={'app.py':b'pass'}):
   with self.assertRaisesRegex(WorkspaceError,'fresh_workspace_manifest'):asyncio.run(self.runtime.start('app.py',dict(path='/',status=200,contains=''),expected_workspace_sha256='a'*64))
  self.assertFalse(self.runtime.root.exists())
 def test_stale_restart_cannot_stop_existing_service(self):
  with patch.object(self.runtime.workspace,'files',return_value={'app.py':b'new'}),patch.object(self.runtime,'stop') as stop:
   with self.assertRaisesRegex(WorkspaceError,'fresh_workspace_manifest'):asyncio.run(self.runtime.restart('a'*32,'b'*64))
   stop.assert_not_called()
 def test_install_receipt_cannot_substitute_requested_package(self):
  value=dict(ok=True,command='install',status='installed_verified',environment_id='a'*32,packages=['other==1'],environment_sha256='b'*64,network=False,install_hooks_executed=False,imports_verified=False,automatic_retry=False)
  with self.assertRaisesRegex(WorkspaceError,'runtime_installed_packages'):validate_runtime_result(value,dict(command='install',pins=['demo_pkg==1.0']))
 def test_capability_requires_exact_safe_shapes(self):
  value=self.runtime.capabilities();value['command']='capabilities';self.assertEqual(validate_runtime_result(value,dict(command='capabilities'))['status'],'capabilities')
  value['services']=[dict(service_id='1234',status='running',outcome_verified=True)]
  with self.assertRaises(WorkspaceError):validate_runtime_result(value,dict(command='capabilities'))
 def test_unsupported_future_wheel_major_refused(self):
  entry=make_wheel(self.wheels,extra={'demo_pkg-1.0.dist-info/WHEEL':b'Wheel-Version: 2.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n'})
  with self.assertRaisesRegex(WorkspaceError,'pure_python_wheel_tag'):wheel_contents(self.wheels/entry['filename'],entry)
 def test_execution_receipt_environment_bound(self):
  from me3_assistant.workspace_history import validate_execution_result
  args=dict(command_id='python',path='main.py');receipt=dict(ok=True,run_id='a'*32,command_id='python',path='main.py',status='completed',exit_code=0,stdout='ok',stderr='',output_truncated=False,elapsed_seconds=1,input_manifest_sha256='b'*64,isolation='bubblewrap_no_network_readonly_snapshot',host_workspace_modified_by_command=False,content_is_untrusted_data=True,environment_sha256='c'*64)
  self.assertEqual(validate_execution_result(receipt,args,'b'*64,'c'*64)['environment_sha256'],'c'*64)
  with self.assertRaisesRegex(WorkspaceError,'execution_receipt_environment'):validate_execution_result(receipt,args,'b'*64,None)
  receipt.pop('environment_sha256');self.assertNotIn('environment_sha256',validate_execution_result(receipt,args,'b'*64,None))
 def terminal_fixture(self,service_id='a'*32,status='stopped',verified=True):
  key='a'*32;target=self.runtime.root/'services'/key;target.mkdir(parents=True,mode=0o700)
  if os.name=='posix':
   os.chmod(self.runtime.root,0o700);os.chmod(target.parent,0o700)
  (target/'spec.json').write_text(json.dumps(dict(service_id=key,workspace_manifest_sha256='b'*64,token='c'*32)))
  state=dict(ok=True,service_id=service_id,status=status,outcome_verified=verified,owned_namespace_closed=verified,workspace_manifest_sha256='b'*64)
  (target/'state.json').write_text(json.dumps(state));sock=target/'control.sock';sock.write_bytes(b'')
  original=Path.lstat
  def fake_lstat(path,*args,**kwargs):
   if path==sock:return type('SocketStat',(),dict(st_mode=stat.S_IFSOCK|0o600,st_uid=os.getuid() if hasattr(os,'getuid') else 0))()
   return original(path,*args,**kwargs)
  return key,target,state,fake_lstat
 def test_durable_terminal_receipt_beats_stale_existing_socket_without_connect(self):
  for status in ('stopped','expired','failed'):
   with self.subTest(status=status):
    key,target,state,fake_lstat=self.terminal_fixture(status=status)
    with patch.object(Path,'lstat',fake_lstat),patch('asyncio.open_unix_connection',new_callable=AsyncMock,create=True) as connect:
     result=asyncio.run(self.runtime.status(key));self.assertEqual(result,state);connect.assert_not_called()
    for file in target.iterdir():file.unlink()
    target.rmdir()
 def test_durable_terminal_receipt_must_match_owned_identity(self):
  key,_,_,fake_lstat=self.terminal_fixture(service_id='d'*32)
  with patch.object(Path,'lstat',fake_lstat),patch('asyncio.open_unix_connection',new_callable=AsyncMock,create=True) as connect:
   with self.assertRaisesRegex(WorkspaceError,'owned_service_state_identity'):asyncio.run(self.runtime.status(key))
   connect.assert_not_called()
 def test_unverified_terminal_label_is_not_proof_of_cleanup(self):
  key,target,_,_=self.terminal_fixture(verified=False);(target/'control.sock').unlink();result=asyncio.run(self.runtime.status(key));self.assertFalse(result['outcome_verified']);self.assertFalse(result['owned_namespace_closed'])

@unittest.skipUnless(sys.platform=='linux' and Path('/usr/bin/bwrap').is_file(),'Actual Linux Bubblewrap required')
class LinuxRuntime(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve();self.wheels=self.root/'wheels';self.wheels.mkdir(mode=0o700);self.workspace=Workspace(self.root/'workspace');self.runtime=Runtime(self.workspace,self.root/'runtime',self.wheels);self.ids=[]
 async def asyncTearDown(self):
  for key in self.ids:
   try:await self.runtime.stop(key)
   except (OSError,WorkspaceError):pass
  self.tmp.cleanup()
 async def test_installed_dependency_imported_in_actual_reusable_python_run(self):
  from me3_assistant.workspace_exec import Executor
  entry=make_wheel(self.wheels);(self.wheels/'approved.json').write_text(json.dumps(dict(version=1,wheels=[entry])));self.runtime.install(['demo_pkg==1.0']);self.workspace.write('main.py','import demo_pkg; print(demo_pkg.VALUE)')
  result=await Executor(self.workspace,self.root/'runs',self.runtime.root).run('python','main.py');self.assertEqual(result['status'],'completed',result);self.assertEqual(result['stdout'].strip(),'42')
 async def test_real_service_health_status_survives_manager_recreation_restart_and_stop(self):
  code='import http.server,os\nclass Handler(http.server.BaseHTTPRequestHandler):\n def do_GET(self):\n  self.send_response(200);self.end_headers();self.wfile.write(b"owned service alive")\nhttp.server.HTTPServer(("127.0.0.1",int(os.environ["ME3_SERVICE_PORT"])),Handler).serve_forever()\n'
  self.workspace.write('app.py',code);manifest=files_manifest(self.workspace.files());started=await self.runtime.start('app.py',dict(path='/',status=200,contains='owned service alive'),lifetime_seconds=30,expected_workspace_sha256=manifest);self.ids.append(started['service_id']);self.assertEqual(started['status'],'running',started);self.assertTrue(started['outcome_verified'])
  another=Runtime(self.workspace,self.runtime.root,self.wheels);status=await another.status(started['service_id']);self.assertEqual(status['status'],'running');self.assertTrue(status['health']['healthy'])
  renewed=await another.restart(started['service_id'],manifest);self.ids.append(renewed['service_id']);self.assertEqual(renewed['replaces_service_id'],started['service_id']);self.assertEqual(renewed['status'],'running',renewed);self.assertNotEqual(renewed['service_id'],started['service_id']);started=renewed
  self.workspace.write('app.py',code.replace('owned service alive','changed service alive'),self.workspace.listing()['files'][0]['sha256']);newmanifest=files_manifest(self.workspace.files())
  # New source requires explicit current manifest, and an exact new health spec.
  old=await another.stop(started['service_id']);self.assertEqual(old['status'],'stopped');self.assertTrue(old['owned_namespace_closed'])
  restarted=await another.start('app.py',dict(path='/',status=200,contains='changed service alive'),lifetime_seconds=30,expected_workspace_sha256=newmanifest);self.ids.append(restarted['service_id']);self.assertTrue(restarted['health']['healthy']);stopped=await another.stop(restarted['service_id']);self.assertTrue(stopped['owned_namespace_closed'])
 async def test_worker_expiry_stops_owned_namespace(self):
  self.workspace.write('app.py','import http.server,os\nhttp.server.HTTPServer(("127.0.0.1",int(os.environ["ME3_SERVICE_PORT"])),http.server.SimpleHTTPRequestHandler).serve_forever()')
  started=await self.runtime.start('app.py',dict(path='/',status=200,contains=''),lifetime_seconds=10,expected_workspace_sha256=files_manifest(self.workspace.files()));self.ids.append(started['service_id']);await asyncio.sleep(10.2);result=await self.runtime.status(started['service_id']);self.assertEqual(result['status'],'expired');self.assertTrue(result['owned_namespace_closed'])
if __name__=='__main__':unittest.main()
