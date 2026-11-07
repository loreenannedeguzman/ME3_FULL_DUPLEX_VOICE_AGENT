"""Bounded Python catalogue in a Pi Bubblewrap namespace. Never a host shell."""
import asyncio,json,os,signal,sys,time,uuid,anyio
from pathlib import Path
from me3_assistant.workspace import WorkspaceError,relative,digest
from me3_assistant.workspace_validation import validate_command_parameters,validate_command_targets
PROJECT_COMMANDS={'python_compile','python_dependencies','python_http_check'}
PROJECT_TEST_COMMAND='python_project_tests'

BOOTSTRAP='''import os,resource,runpy,sys,unittest
resource.setrlimit(resource.RLIMIT_CPU,(2,2))
resource.setrlimit(resource.RLIMIT_AS,(268435456,268435456))
resource.setrlimit(resource.RLIMIT_FSIZE,(1048576,1048576))
resource.setrlimit(resource.RLIMIT_NOFILE,(32,32))
resource.setrlimit(resource.RLIMIT_NPROC,(8,8))
sys.path.insert(0,"/workspace")
kind,path,*args=sys.argv[1:]
if kind=="python":
 sys.argv=[path,*args];runpy.run_path(path,run_name="__main__")
else:
 # A test-file path selects that module; a directory keeps normal discovery.
 start,pattern=(os.path.dirname(path),os.path.basename(path)) if os.path.isfile(path) else (path,"test*.py")
 suite=unittest.defaultTestLoader.discover(start,pattern=pattern)
 result=unittest.TextTestRunner(verbosity=2).run(suite)
 if result.skipped: print("Required test run contains skipped checks.",file=sys.stderr)
 sys.exit(0 if result.wasSuccessful() and result.testsRun and not result.skipped else 1)
'''

PROJECT_TEST_BOOTSTRAP='''import contextlib,io,json
sys.path.insert(0,"/workspace")
kind,path,*args=sys.argv[1:]
class Capture(io.TextIOBase):
 def __init__(self):self.bytes=0
 def write(self,text):self.bytes+=len(text.encode("utf-8","replace"));return len(text)
 def flush(self):pass
class Compact(unittest.TestResult):
 def __init__(self):
  super().__init__();self.successes=0;self.failure_events=0;self.error_events=0;self.skip_count=0;self.expected_count=0;self.unexpected_count=0;self.diagnostics=[];self.truncated=False
 def diagnostic(self,test,kind,error=None):
  if len(self.diagnostics)>=2:self.truncated=True;return
  try:identifier=str(test.id())[:100]
  except BaseException:identifier="unavailable"
  try:message=str(error[1])[:200] if error else "Unexpected success"
  except BaseException:message="Exception text unavailable"
  self.diagnostics.append(dict(test=identifier,kind=kind,exception=error[0].__name__[:64] if error else "",message=message))
 def addSuccess(self,test):self.successes+=1
 def addError(self,test,error):
  self.error_events+=1
  if not self.errors:self.errors.append((test,"bounded diagnostic"))
  self.diagnostic(test,"error",error)
 def addFailure(self,test,error):
  self.failure_events+=1
  if not self.failures:self.failures.append((test,"bounded diagnostic"))
  self.diagnostic(test,"failure",error)
 def addSubTest(self,test,subtest,error):
  if error:
   if issubclass(error[0],test.failureException):self.addFailure(subtest,error)
   else:self.addError(subtest,error)
 def addSkip(self,test,reason):
  self.skip_count+=1
  if not self.skipped:self.skipped.append((test,"bounded skip"))
 def addExpectedFailure(self,test,error):
  self.expected_count+=1
  if not self.expectedFailures:self.expectedFailures.append((test,"expected failure"))
 def addUnexpectedSuccess(self,test):
  self.unexpected_count+=1
  if not self.unexpectedSuccesses:self.unexpectedSuccesses.append(test)
  self.diagnostic(test,"unexpected_success")
result=Compact();capture=Capture();expected=0
with contextlib.redirect_stdout(capture),contextlib.redirect_stderr(capture):
 try:
  start,pattern=(os.path.dirname(path),os.path.basename(path)) if os.path.isfile(path) else (path,"test*.py")
  suite=unittest.defaultTestLoader.discover(start,pattern=pattern);expected=suite.countTestCases();suite.run(result)
 except Exception:
  result.addError(unittest.FunctionTestCase(lambda:None),sys.exc_info())
complete=result.testsRun==expected and not result.shouldStop
success=result.wasSuccessful() and bool(result.testsRun) and not result.skip_count and complete and result.successes+result.expected_count==result.testsRun
summary=dict(framework="unittest",discovered_tests=expected,tests_run=result.testsRun,successes=result.successes,failure_events=result.failure_events,error_events=result.error_events,skipped=result.skip_count,expected_failures=result.expected_count,unexpected_successes=result.unexpected_count,complete=complete,success=success,captured_output_bytes=capture.bytes,diagnostics=result.diagnostics,diagnostics_truncated=result.truncated)
raw=json.dumps(summary,ensure_ascii=True,separators=(",",":"))
while len(raw.encode())>1400:
 summary["diagnostics_truncated"]=True
 if len(summary["diagnostics"])>1:summary["diagnostics"].pop()
 elif summary["diagnostics"]:
  item=summary["diagnostics"][0]
  if max(len(item[k]) for k in ("test","message","exception"))>16:
   for key in ("test","message","exception"):item[key]=item[key][:max(16,len(item[key])//2)]
  else:summary["diagnostics"].pop()
 else:raise RuntimeError("project_test_summary_limit")
 raw=json.dumps(summary,ensure_ascii=True,separators=(",",":"))
print(raw)
sys.exit(0 if success else 1)
'''

def command(snapshot,kind,path,args,packages=None):
    if sys.platform!='linux' or not Path('/usr/bin/bwrap').is_file():raise WorkspaceError('isolated_execution_unavailable')
    cmd=['/usr/bin/bwrap','--unshare-all','--unshare-user','--die-with-parent','--new-session','--cap-drop','ALL','--clearenv','--setenv','PATH','/usr/bin','--setenv','HOME','/tmp','--setenv','LANG','C.UTF-8']
    for p in ['/usr','/lib','/lib64','/bin']:
        if Path(p).exists():cmd+=['--ro-bind',p,p]
    bootstrap=BOOTSTRAP
    if kind in PROJECT_COMMANDS:
        from me3_assistant.workspace_project import BOOTSTRAP as project_bootstrap
        bootstrap=BOOTSTRAP.split('sys.path.insert(0,')[0].replace('(2,2)','(8,8)')+project_bootstrap
    if kind==PROJECT_TEST_COMMAND:
        bootstrap=BOOTSTRAP.split('sys.path.insert(0,')[0].replace('(2,2)','(8,8)').replace('(268435456,268435456)','(536870912,536870912)').replace('(1048576,1048576)','(16777216,16777216)')+PROJECT_TEST_BOOTSTRAP
    cmd+=['--proc','/proc','--dev','/dev','--remount-ro','/dev','--size','33554432' if kind==PROJECT_TEST_COMMAND else '8388608','--tmpfs','/tmp','--ro-bind',str(snapshot),'/workspace']
    if packages is not None:cmd+=['--ro-bind',str(packages),'/packages'];bootstrap='import sys;sys.path.insert(0,"/packages")\n'+bootstrap
    cmd+=['--chdir','/workspace','--remount-ro','/','--','/usr/bin/python3','-I','-B','-c',bootstrap,kind,(path if kind in PROJECT_COMMANDS else '/workspace/'+path),*args]
    return cmd

class Executor:
    def __init__(self,workspace,runs,runtime_root=None):self.workspace=workspace;self.runs=Path(runs);self.busy=False;self.runtime_root=Path(runtime_root) if runtime_root is not None else workspace.root.parent/'agent-runtime';self.environment_sha256=None
    async def run(self,kind,path,arguments=None,stdin='',timeout_seconds=2):
        args=validate_command_parameters(kind,path,arguments,stdin,timeout_seconds)
        if self.busy:raise WorkspaceError('workspace_execution_busy')
        # No process or run directory is created when isolation is unavailable.
        from me3_assistant.workspace_runtime import active_packages,active_environment_sha256
        packages=active_packages(self.runtime_root)
        self.environment_sha256=active_environment_sha256(self.runtime_root)
        command(Path('/unused'),kind,path,args,packages)
        self.busy=True;proc=None;run=None;started=time.monotonic();buffers={'stdout':bytearray(),'stderr':bytearray()};state='starting';output_limit=False
        try:
            files=self.workspace.files()
            validate_command_targets(kind,path,args,files)
            # Administrator-selected run archive is outside the mounted workspace.
            self.runs.mkdir(mode=0o700,parents=True,exist_ok=True)
            if self.runs.is_symlink() or self.runs.resolve()!=self.runs.absolute():raise WorkspaceError('execution_archive_link')
            if len(list(self.runs.iterdir()))>=32:raise WorkspaceError('execution_archive_limit')
            run=self.runs/uuid.uuid4().hex;run.mkdir(mode=0o700);snapshot=run/'input';snapshot.mkdir()
            for name,raw in files.items():
                p=snapshot/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
            async def collect(stream,name):
                nonlocal output_limit
                while True:
                    part=await stream.read(256)
                    if not part:return
                    used=sum(map(len,buffers.values()));remaining=max(0,1500-used);buffers[name].extend(part[:remaining])
                    if len(part)>remaining:output_limit=True;raise WorkspaceError('command_output_limit')
            async def terminate():
                # The owned group may outlive its already-exited leader.
                try:os.killpg(proc.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                await proc.wait()
            # Check immutable installed bytes again immediately before mounting.
            if active_packages(self.runtime_root)!=packages:raise WorkspaceError('active_environment_changed_before_dispatch')
            spawning=asyncio.create_task(asyncio.create_subprocess_exec(*command(snapshot,kind,path,args,packages),stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,env={'PATH':'/usr/bin','LANG':'C.UTF-8'},start_new_session=True))
            try:proc=await asyncio.shield(spawning)
            except asyncio.CancelledError:
                state='canceled'
                with anyio.CancelScope(shield=True):
                    proc=await spawning;await terminate()
                raise
            async def feed():
                try:proc.stdin.write(stdin.encode());await proc.stdin.drain()
                except (BrokenPipeError,ConnectionResetError):pass
                finally:proc.stdin.close()
            tasks=[asyncio.create_task(c) for c in [feed(),collect(proc.stdout,'stdout'),collect(proc.stderr,'stderr'),proc.wait()]]
            try:
                async with asyncio.timeout(timeout_seconds):await asyncio.gather(*tasks)
                state='completed' if proc.returncode==0 else 'failed'
            except TimeoutError:state='timed_out'
            except WorkspaceError:state='output_limit'
            except asyncio.CancelledError:state='canceled';raise
            finally:
                with anyio.CancelScope(shield=True):
                    await terminate()
                    for task in tasks:
                        if not task.done():task.cancel()
                    await asyncio.gather(*tasks,return_exceptions=True)
            return self.result(run,kind,path,files,state,proc,buffers,started,output_limit)
        finally:
            if run is not None:
                result=self.result(run,kind,path,files,state,proc,buffers,started,output_limit)
                (run/'receipt.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
            self.busy=False
    def result(self,run,kind,path,files,state,proc,buffers,started,truncated):
        clean=lambda data:''.join(c if ord(c)>=32 or c in '\n\t' else '?' for c in bytes(data).decode('utf-8','replace'))
        result={'ok':True,'run_id':run.name,'command_id':kind,'path':path,'status':state,'exit_code':proc.returncode if proc else None,'stdout':clean(buffers['stdout']),'stderr':clean(buffers['stderr']),'output_truncated':truncated,'elapsed_seconds':round(time.monotonic()-started,4),'input_manifest_sha256':digest(json.dumps({n:digest(raw) for n,raw in sorted(files.items())},sort_keys=True).encode()),'isolation':'bubblewrap_no_network_readonly_snapshot','host_workspace_modified_by_command':False,'content_is_untrusted_data':True}
        if self.environment_sha256 is not None:result['environment_sha256']=self.environment_sha256
        return result
