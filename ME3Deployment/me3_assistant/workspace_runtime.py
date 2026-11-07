"""Approved offline Python environments and owned, namespace-local services.

All roots are administrator supplied. Model inputs are relative artifact paths,
exact catalogue package pins, generated environment/service IDs and bounded
health checks. No shell, downloads, host interpreter execution of artifact code,
host listening port, account access, process-by-name killing or automatic retry.
"""
import asyncio, email.parser, hashlib, inspect, json, math, os, re, shutil, signal
import socket, stat, subprocess, sys, time, uuid, zipfile, select, base64
from pathlib import Path, PurePosixPath
from me3_assistant.workspace import Workspace, WorkspaceError, relative, digest

ID=re.compile('[0-9a-f]{32}')
PIN=re.compile(r'([A-Za-z0-9][A-Za-z0-9_.-]{0,79})==([A-Za-z0-9_.+!-]{1,40})')
MAX_WHEEL_BYTES=8*1024*1024
MAX_ENV_BYTES=32*1024*1024
VIEW_BODY_MAX=16384
VIEW_RESPONSE_MAX=65536
VIEW_REQUEST_MAX=24576
VIEW_WIRE_MAX=102400
VIEW_TYPES=('text/html','text/css','text/javascript','application/javascript','application/json','text/plain','image/png','image/jpeg','image/gif','image/webp','image/svg+xml')

def view_request(value):
 """Pure, standalone validation also embedded in the networkless namespace."""
 import base64,re
 from urllib.parse import urlsplit,unquote
 def check(ok):
  if not ok:raise ValueError('invalid_runtime_view_request')
 check(type(value) is dict and set(value)=={'method','path','content_type','body_base64'})
 method,path,kind,encoded=(value[k] for k in ('method','path','content_type','body_base64'))
 check(method in ('GET','POST') and type(path) is str and 1<=len(path)<=2048)
 check(path.startswith('/') and not path.startswith('//') and '\\' not in path and '#' not in path and all(32<ord(c)<127 for c in path))
 parsed=urlsplit(path);check(not parsed.scheme and not parsed.netloc)
 check(re.search(r'%(?![0-9A-Fa-f]{2})',path) is None and '%25' not in parsed.path.lower())
 decoded=unquote(parsed.path,errors='strict')
 check(decoded.startswith('/') and not decoded.startswith('//') and '\\' not in decoded)
 check(not any(part in ('.','..') for part in decoded.split('/')) and not any(ord(c)<=32 or ord(c)==127 for c in unquote(path,errors='strict')))
 check(type(encoded) is str and len(encoded)<=21848)
 body=base64.b64decode(encoded,validate=True);check(len(body)<=16384 and base64.b64encode(body).decode()==encoded)
 check((method=='GET' and kind is None and not body) or (method=='POST' and kind in ('application/json','application/x-www-form-urlencoded','text/plain')))
 return dict(method=method,path=path,content_type=kind,body_base64=encoded)

def need(value,reason):
 if not value:raise WorkspaceError(reason)
def plain(path):
 info=path.lstat();need(stat.S_ISREG(info.st_mode) and info.st_nlink==1,'runtime_plain_file_required')
 return info
def folder(path):
 path=Path(path).absolute();need(not path.is_symlink() and not any(p.is_symlink() for p in path.parents),'runtime_directory_link')
 path.mkdir(parents=True,mode=0o700,exist_ok=True)
 need(path.resolve()==path and path.is_dir(),'runtime_directory_required')
 if os.name=='posix':need(path.stat().st_uid==os.getuid() and not path.stat().st_mode&0o077,'runtime_private_directory_required')
 return path
def read(path,limit=65536):
 need(plain(path).st_size<=limit,'runtime_record_bound');return json.loads(path.read_bytes())
def save(path,value):
 raw=(json.dumps(value,sort_keys=True,ensure_ascii=False)+'\n').encode();need(len(raw)<=65536,'runtime_record_bound')
 if path.exists():plain(path)
 temporary=path.with_name('.'+path.name+'-'+uuid.uuid4().hex)
 with temporary.open('xb') as stream:os.chmod(temporary,0o600);stream.write(raw);stream.flush();os.fsync(stream.fileno())
 os.replace(temporary,path)
def identifier(value):need(isinstance(value,str) and ID.fullmatch(value),'runtime_identifier_required');return value
def normalized(name):return re.sub('[-_.]+','-',name).lower()
def health(value):
 need(isinstance(value,dict) and set(value)=={'path','status','contains'},'exact_service_health_fields_required')
 path=value['path'];text=value['contains']
 need(isinstance(path,str) and path.startswith('/') and not path.startswith('//') and len(path)<=240 and all(33<=ord(c)<127 for c in path),'local_service_health_path_required')
 need(type(value['status']) is int and 100<=value['status']<=599 and isinstance(text,str) and len(text.encode())<=1000,'bounded_service_health_required')
 return value
def files_manifest(files):return digest(json.dumps({n:digest(v) for n,v in sorted(files.items())},sort_keys=True).encode())

def service_observation(spec,state,*,fresh,current_manifest):
 """Associate the exact snapshot with an observation, never infer liveness."""
 need(state.get('service_id')==spec.get('service_id'),'owned_service_state_identity_mismatch')
 need(state.get('workspace_manifest_sha256') in (None,spec['workspace_manifest_sha256']),'owned_service_state_manifest_mismatch')
 terminal=(state.get('status') in {'stopped','expired','failed'} and state.get('owned_namespace_closed') is True
  and state.get('outcome_verified') is True and state.get('workspace_manifest_sha256')==spec['workspace_manifest_sha256']
  and state.get('environment_id')==spec.get('environment_id'))
 verified_live=(fresh and state.get('status')=='running' and state.get('outcome_verified') is True
  and state.get('health',{}).get('healthy') is True)
 value=dict(service_id=spec['service_id'],status=state['status'] if terminal or verified_live else 'unverified',
  outcome_verified=terminal or verified_live,observation='durable_terminal_receipt' if terminal else 'fresh_status' if fresh else 'cached_record_not_live',
  last_recorded_status=state.get('status'),workspace_manifest_sha256=spec['workspace_manifest_sha256'],
  current_workspace_matches=spec['workspace_manifest_sha256']==current_manifest)
 if spec.get('path') is not None:relative(spec['path']);value['path']=spec['path']
 return value

def process_birth(pid):
 """Birth identity for a just-spawned owned child; never discovers a kill target."""
 try:
  p=Path('/proc')/str(pid);raw=(p/'stat').read_text();fields=raw.rpartition(') ')[2].split()
  value=dict(pid=int(pid),startticks=int(fields[19]),pgid=int(fields[2]),sid=int(fields[3]),uid=p.stat().st_uid,
   boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip())
  if (p/'stat').read_text().rpartition(') ')[2].split()[19]!=str(value['startticks']):return None
  return value
 except (OSError,ValueError,IndexError):return None

def init_details(pid):
 birth=process_birth(pid);need(birth is not None,'owned_init_birth_unavailable')
 p=Path('/proc')/str(pid);fields=(p/'stat').read_text().rpartition(') ')[2].split()
 status=(p/'status').read_text();need(len(status)<=32768,'owned_init_status_bound')
 rows=dict(line.split(':',1) for line in status.splitlines() if ':' in line)
 birth.update(ppid=int(fields[1]),nspid=[int(v) for v in rows.get('NSpid','').split()])
 return birth

def strict_birth_observation(birth,proc=Path('/proc')):
 """Only absence or a different birth proves this exact process has ended.

 Permission/parse failures propagate; process_birth(None) is never death proof.
 """
 need(type(birth) is dict and all(type(birth.get(k)) is int and birth[k]>0 for k in ('pid','startticks','pgid','sid')) and type(birth.get('uid')) is int and birth['uid']>=0,'recovery_birth_invalid')
 need(type(birth.get('boot_id')) is str and re.fullmatch('[0-9a-f-]{36}',birth['boot_id']),'recovery_boot_invalid')
 boot=(proc/'sys/kernel/random/boot_id').read_text().strip();need(re.fullmatch('[0-9a-f-]{36}',boot),'current_boot_invalid')
 p=proc/str(birth['pid'])
 try:
  info=p.stat();raw=(p/'stat').read_text();fields=raw.rpartition(') ')[2].split()
  need(raw.startswith(str(birth['pid'])+' (') and len(fields)>=20 and fields[19].isdigit(),'current_process_stat_invalid')
  current=dict(pid=birth['pid'],startticks=int(fields[19]),uid=info.st_uid,boot_id=boot)
  need((p/'stat').read_text()==raw and p.stat().st_uid==info.st_uid,'current_process_birth_changed_during_read')
 except FileNotFoundError:return dict(ended=True,reason='exact_proc_entry_absent',recorded_birth=birth,current_birth=None)
 need((proc/'sys/kernel/random/boot_id').read_text().strip()==boot,'current_boot_changed')
 same=all(current[k]==birth[k] for k in current)
 return dict(ended=not same,reason='exact_birth_still_present' if same else 'different_process_birth',recorded_birth=birth,current_birth=current)

def pidfd_ready(fd,timeout=0):return bool(select.select([fd],[],[],timeout)[0])

class BlockedNamespace:
 """One new bwrap clone, held before artifact execution until pidfd binding.

 v0.12.0 writes info before releasing clone setup; block-fd is consumed before
 PID1 forks the artifact. A second inherited write end prevents EOF from
 accidentally releasing the clone if this owner fails before explicit release.
 """
 def __init__(self,make_command,errors):
  need(hasattr(os,'pidfd_open') and hasattr(signal,'pidfd_send_signal'),'kernel_pidfd_required')
  self.process=None;self.pidfd=None;self.info=None;self.birth=None;self.verified=False;self.released=False
  self.closed=False;self.cleanup_started=False;self.signals=0;self._fds=set()
  info_r,info_w=os.pipe();block_r,block_w=os.pipe();self._fds.update((info_r,info_w,block_r,block_w));self.block_w=block_w
  try:
   command=make_command(info_w,block_r,block_w)
   self.process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=errors,
    env=dict(PATH='/usr/bin:/bin',LANG='C.UTF-8'),start_new_session=True,pass_fds=(info_w,block_r,block_w))
   for fd in (info_w,block_r):os.close(fd);self._fds.remove(fd)
   deadline=time.monotonic()+3;raw=bytearray()
   while True:
    need(time.monotonic()<deadline and select.select([info_r],[],[],max(0,deadline-time.monotonic()))[0],'owned_bwrap_info_timeout')
    part=os.read(info_r,8193-len(raw));raw.extend(part);need(len(raw)<=8192,'owned_bwrap_info_bound')
    if not part:break
   os.close(info_r);self._fds.remove(info_r)
   def pairs(items):
    value={}
    for k,v in items:need(k not in value,'owned_bwrap_info_duplicate');value[k]=v
    return value
   self.info=json.loads(raw,object_pairs_hook=pairs);pid=self.info.get('child-pid');inode=self.info.get('pid-namespace')
   need(type(pid) is int and pid>1 and type(inode) is int and inode>0 and inode!=Path('/proc/self/ns/pid').stat().st_ino,'owned_bwrap_private_pid_namespace_required')
   before=init_details(pid);need(before['ppid']==self.process.pid,'owned_bwrap_clone_parent_mismatch')
   self.pidfd=os.pidfd_open(pid,0)
   fdinfo=(Path('/proc/self/fdinfo')/str(self.pidfd)).read_text()
   reported=dict(line.split(':',1) for line in fdinfo.splitlines() if ':' in line)
   after=init_details(pid)
   immutable=lambda row:tuple(row.get(k) for k in ('pid','startticks','boot_id','ppid','nspid'))
   need(immutable(before)==immutable(after) and int(reported.get('Pid','-1'))==pid and not pidfd_ready(self.pidfd),'owned_init_pidfd_binding_changed')
   self.birth=after
   need(len(after['nspid'])>=2 and after['nspid'][0]==pid and after['nspid'][-1]==1,'private_namespace_init_required')
   self.verified=True
  except BaseException:
   self.close();self.dispose();raise
 def release(self):
  need(self.verified and not self.released and not self.cleanup_started and self.process.poll() is None and not pidfd_ready(self.pidfd),'owned_namespace_release_invalid')
  need(os.write(self.block_w,b'1')==1,'owned_namespace_release_failed');self.released=True
  os.close(self.block_w);self._fds.remove(self.block_w)
 def receipt(self):
  return dict(process=process_birth(self.process.pid),init=self.birth,pid_namespace_inode=self.info.get('pid-namespace') if self.info else None,
   capture_before_artifact_release=self.verified,released=self.released,pidfd_readable=pidfd_ready(self.pidfd) if self.pidfd is not None else None,
   shutdown_signals=self.signals,closed=self.closed)
 def close(self):
  if self.cleanup_started:return self.closed
  self.cleanup_started=True
  try:
   if self.pidfd is not None and self.birth is not None:
    if not pidfd_ready(self.pidfd):signal.pidfd_send_signal(self.pidfd,signal.SIGKILL);self.signals+=1
    dead=pidfd_ready(self.pidfd,3)
   else:dead=False
   if self.process is not None:
    # Exact Popen child only on failed setup, never a discovered service/PID.
    if not dead and not self.verified and self.process.poll() is None:self.process.kill()
    self.process.wait(timeout=3)
   self.closed=bool(self.verified and dead and self.process is not None and self.process.poll() is not None)
  except (OSError,subprocess.TimeoutExpired):self.closed=False
  finally:
   for fd in tuple(self._fds):os.close(fd);self._fds.remove(fd)
  return self.closed
 def dispose(self):
  if self.pidfd is not None:os.close(self.pidfd);self.pidfd=None

def wheel_contents(path,entry):
 """Inspect/extract bytes only; no installer, hooks, scripts or wheel imports."""
 need(set(entry)=={'filename','sha256','name','version'},'exact_approved_wheel_fields_required')
 need(isinstance(entry['filename'],str) and re.fullmatch('[A-Za-z0-9_.+!-]+-py3-none-any.whl',entry['filename']) and path.name==entry['filename'],'approved_pure_python_wheel_required')
 need(plain(path).st_size<=MAX_WHEEL_BYTES and re.fullmatch('[0-9a-f]{64}',entry['sha256']) and digest(path.read_bytes())==entry['sha256'],'approved_wheel_hash_mismatch')
 with zipfile.ZipFile(path) as archive:
  members=archive.infolist();need(1<=len(members)<=1000 and sum(v.file_size for v in members)<=MAX_ENV_BYTES,'wheel_expansion_bound');result={}
  for item in members:
   name=item.filename
   need(re.fullmatch('[A-Za-z0-9_-][A-Za-z0-9_./+!-]*',name) and not name.startswith('/') and '\\' not in name and not any(p in ('','.','..') for p in PurePosixPath(name).parts) and len(name)<=240,'wheel_relative_path_required')
   need(not any(p.endswith('.data') for p in PurePosixPath(name).parts) and not name.lower().endswith(('.pth','.so','.dll','.dylib','.exe','.pyc')),'wheel_executable_hook_or_native_file_refused')
   mode=item.external_attr>>16;need(not stat.S_ISLNK(mode),'wheel_link_refused')
   if item.is_dir():continue
   need(name not in result and item.file_size<=MAX_WHEEL_BYTES,'wheel_duplicate_or_file_bound')
   result[name]=archive.read(item)
  metadata=[n for n in result if n.endswith('.dist-info/METADATA')];tags=[n for n in result if n.endswith('.dist-info/WHEEL')]
  need(len(metadata)==len(tags)==1 and metadata[0].rsplit('/',1)[0]==tags[0].rsplit('/',1)[0],'wheel_metadata_required')
  msg=email.parser.BytesParser().parsebytes(result[metadata[0]]);wheel=email.parser.BytesParser().parsebytes(result[tags[0]])
  need(normalized(msg['Name'])==normalized(entry['name']) and msg['Version']==entry['version'],'approved_wheel_identity_mismatch')
  need(isinstance(wheel['Wheel-Version'],str) and re.fullmatch(r'1\.[0-9]+',wheel['Wheel-Version']) and wheel['Root-Is-Purelib']=='true' and set(wheel.get_all('Tag',[]))=={'py3-none-any'},'pure_python_wheel_tag_required')
  need(metadata[0].rsplit('/',1)[0]+'/RECORD' in result,'wheel_RECORD_required')
  requires=msg.get_all('Requires-Dist',[]);need(len(requires)<=16 and all(PIN.fullmatch(v.strip()) for v in requires),'only_exact_approved_transitive_dependencies_supported')
  return result,[v.strip() for v in requires]

class Runtime:
 def __init__(self,workspace,root,wheelhouse):
  self.workspace=workspace;self.root=Path(root).absolute();self.wheelhouse=Path(wheelhouse).absolute()
 def available(self):
  return sys.platform=='linux' and Path('/usr/bin/bwrap').is_file() and hasattr(socket,'AF_UNIX')
 def catalogue(self):
  path=self.wheelhouse/'approved.json';need(path.exists(),'approved_offline_wheel_catalogue_unavailable')
  need(self.wheelhouse.resolve()==self.wheelhouse and not any(p.is_symlink() for p in [self.wheelhouse,*self.wheelhouse.parents]),'wheelhouse_directory_link')
  raw=read(path);need(isinstance(raw,dict) and set(raw)=={'version','wheels'} and raw['version']==1 and isinstance(raw['wheels'],list) and len(raw['wheels'])<=32,'bounded_approved_wheel_catalogue_required')
  entries={}
  for entry in raw['wheels']:
   need(isinstance(entry,dict) and set(entry)=={'filename','sha256','name','version'} and isinstance(entry['name'],str) and isinstance(entry['version'],str) and PIN.fullmatch(entry['name']+'=='+entry['version']),'exact_approved_wheel_fields_required');pin=normalized(entry['name'])+'=='+entry['version'];need(pin not in entries,'duplicate_approved_wheel_pin');entries[pin]=entry
  return entries
 def install(self,pins):
  need(isinstance(pins,list) and 1<=len(pins)<=8 and len(set(pins))==len(pins) and all(isinstance(p,str) and PIN.fullmatch(p) for p in pins),'exact_bounded_package_pins_required')
  approved=self.catalogue();todo=list(pins);selected={};contents={}
  while todo:
   pin=todo.pop();name,version=PIN.fullmatch(pin).groups();key=normalized(name)+'=='+version
   if key in selected:continue
   need(len(selected)<8 and key in approved,'dependency_not_in_approved_offline_catalogue');entry=approved[key]
   need(not any(normalized(v['name'])==normalized(name) for v in selected.values()),'conflicting_dependency_versions')
   files,requires=wheel_contents(self.wheelhouse/entry['filename'],entry)
   need(not set(files)&set(contents),'wheel_file_collision');contents.update(files);selected[key]=entry;todo+=requires
   need(sum(map(len,contents.values()))<=MAX_ENV_BYTES,'environment_expansion_bound')
  envs=folder(folder(self.root)/'environments');need(len(list(envs.iterdir()))<16,'environment_archive_bound');key=uuid.uuid4().hex;target=envs/key;target.mkdir(mode=0o700);site=folder(target/'site')
  try:
   for name,raw in contents.items():
    p=site/name;p.parent.mkdir(parents=True,mode=0o700,exist_ok=True);p.write_bytes(raw);os.chmod(p,0o600)
   actual={p.relative_to(site).as_posix():digest(p.read_bytes()) for p in site.rglob('*') if p.is_file()}
   expected={n:digest(raw) for n,raw in contents.items()};need(actual==expected,'environment_fresh_readback_failed')
   receipt=dict(ok=True,environment_id=key,status='installed_verified',packages=sorted(selected),files_sha256=expected,environment_sha256=digest(json.dumps(expected,sort_keys=True).encode()),network=False,install_hooks_executed=False,imports_verified=False)
   save(target/'receipt.json',receipt);save(self.root/'active-environment.json',dict(environment_id=key));return {k:v for k,v in receipt.items() if k!='files_sha256'}
  except BaseException:
   save(target/'failed.json',dict(status='installation_failed_no_retry'));raise
 def environment(self,key):
  if key is None:return None
  target=folder(folder(self.root)/'environments')/identifier(key);need(target.is_dir() and not target.is_symlink(),'environment_not_found');receipt=read(target/'receipt.json');need(receipt['environment_id']==key and receipt['status']=='installed_verified','verified_environment_required')
  site=folder(target/'site');need(not any(p.is_symlink() for p in site.rglob('*')),'environment_link_refused')
  actual={p.relative_to(site).as_posix():digest(p.read_bytes()) for p in site.rglob('*') if p.is_file()};need(actual==receipt['files_sha256'],'installed_environment_changed');return site
 def service_dir(self,key):
  path=folder(folder(self.root)/'services')/identifier(key);need(path.is_dir() and not path.is_symlink(),'owned_service_not_found');return path
 def owner_loss(self,target,spec,*,reconcile=False):
  """Proof-bound failed-owner recovery. No signals, socket deletion or replay.

  Preserve the original state and write one separate immutable recovery record.
  Legacy/incomplete identities and unreadable /proc evidence remain uncertain.
  """
  fd=None
  try:
   import fcntl
   need(sys.platform=='linux' and target==self.service_dir(spec['service_id']),'recovery_service_root_mismatch')
   fd=os.open(target,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
   need((os.fstat(fd).st_dev,os.fstat(fd).st_ino)==(target.stat().st_dev,target.stat().st_ino),'recovery_service_directory_changed')
   names=('spec.json','state.json','worker-owner.json','namespace-owner.json')
   need(all(plain(target/n).st_uid==os.getuid() and not plain(target/n).st_mode&0o077 for n in names),'recovery_private_records_required')
   pins={n:digest((target/n).read_bytes()) for n in names};records={n:read(target/n) for n in names}
   need(records['spec.json']==spec,'recovery_spec_changed');state=records['state.json'];owner=records['namespace-owner.json'];worker=records['worker-owner.json']
   key=identifier(spec['service_id']);relative(spec['path']);health(spec['health'])
   need(target.name==key and state.get('service_id')==owner.get('service_id')==worker.get('service_id')==key,'recovery_service_identity_mismatch')
   need(state.get('workspace_manifest_sha256')==spec['workspace_manifest_sha256'] and state.get('environment_id')==spec.get('environment_id'),'recovery_state_snapshot_mismatch')
   snapshot=target/'input';need(snapshot.is_dir() and not snapshot.is_symlink() and owner.get('snapshot')==worker.get('snapshot')==str(snapshot),'recovery_snapshot_path_mismatch')
   need(files_manifest(Workspace(snapshot).files())==spec['workspace_manifest_sha256'],'recovery_snapshot_changed')
   environment=self.environment(spec.get('environment_id'));need(spec.get('environment_path')==(str(environment) if environment else None),'recovery_environment_mismatch')
   need(owner.get('capture_before_artifact_release') is True and owner.get('released') is True and owner.get('closed') is False,'recovery_capture_proof_required')
   init=owner.get('init');process=owner.get('process');birth=worker.get('worker')
   need(type(init) is dict and type(process) is dict and type(birth) is dict,'recovery_exact_births_required')
   need(type(init.get('nspid')) is list and len(init['nspid'])>=2 and all(type(v) is int and v>0 for v in init['nspid']) and init['nspid'][0]==init.get('pid') and init['nspid'][-1]==1,'recovery_private_PID1_proof_required')
   inode=owner.get('pid_namespace_inode');need(type(inode) is int and inode>0 and inode!=Path('/proc/self/ns/pid').stat().st_ino,'recovery_private_namespace_required')
   need(init.get('ppid')==process.get('pid') and init.get('pgid')==process.get('pgid') and init.get('sid')==process.get('sid') and len({init.get('pid'),process.get('pid'),birth.get('pid')})==3,'recovery_namespace_parent_mismatch')
   need(init.get('boot_id')==process.get('boot_id')==birth.get('boot_id') and init.get('uid')==process.get('uid')==birth.get('uid')==os.getuid(),'recovery_namespace_owner_mismatch')
   result=dict(ok=False,service_id=key,path=spec['path'],status='failed',health=dict(healthy=False,error='owned_namespace_owner_lost'),outcome_verified=True,owned_namespace_closed=True,workspace_manifest_sha256=spec['workspace_manifest_sha256'],environment_id=spec.get('environment_id'),namespace_local_only=True,host_listening_port=False,expires_at=spec['created_at']+spec['lifetime_seconds'],automatic_retry=False)
   path=target/'owner-loss.json'
   if path.exists():
    receipt=read(path);need(receipt.get('status')=='OWNED_NAMESPACE_OWNER_LOSS_RECONCILED' and receipt.get('inputs_sha256')==pins and receipt.get('original_state')==state and receipt.get('service_id')==key,'recovery_receipt_changed')
    need(receipt.get('proof',{}).get('worker',{}).get('ended') is True and receipt.get('proof',{}).get('namespace_init',{}).get('ended') is True and receipt.get('result')==result,'recovery_terminal_proof_required')
    return receipt['result']
   if not reconcile:return None
   proof=dict(worker=strict_birth_observation(birth),namespace_init=strict_birth_observation(init))
   need(all(row['ended'] is True for row in proof.values()),'recovery_owner_still_present')
   need(all(digest((target/n).read_bytes())==pin for n,pin in pins.items()),'recovery_inputs_changed')
   receipt=dict(status='OWNED_NAMESPACE_OWNER_LOSS_RECONCILED',service_id=key,inputs_sha256=pins,original_state=state,proof=proof,result=result,observed_at=time.time(),signals=0,service_requests=0,automatic_retry=False,graceful_shutdown=False)
   need(not path.exists(),'recovery_record_already_exists');save(path,receipt);return result
  except (ImportError,OSError,WorkspaceError,ValueError,KeyError,TypeError,IndexError):return None
  finally:
   if fd is not None:os.close(fd)
 def readiness_identity(self,key,expected_workspace_sha256):
  """Host-only identity, not an action capability. Never discovers a kill PID."""
  target=self.service_dir(key);spec=read(target/'spec.json')
  need(spec['service_id']==key and spec['workspace_manifest_sha256']==expected_workspace_sha256,'readiness_service_snapshot_changed')
  need(time.time()<spec['created_at']+spec['lifetime_seconds'],'readiness_service_expired')
  owner=read(target/'namespace-owner.json');worker=read(target/'worker-owner.json')
  need(owner.get('service_id')==worker.get('service_id')==key and owner.get('snapshot')==worker.get('snapshot')==str(target/'input'),'readiness_owner_binding_changed')
  need(owner.get('capture_before_artifact_release') is True and owner.get('released') is True and owner.get('closed') is False,'readiness_released_owner_required')
  init=owner.get('init');process=owner.get('process');birth=worker.get('worker');inode=owner.get('pid_namespace_inode')
  need(type(init) is dict and type(process) is dict and type(birth) is dict and type(inode) is int and inode>0 and inode!=Path('/proc/self/ns/pid').stat().st_ino,'readiness_private_namespace_required')
  need(type(init.get('nspid')) is list and len(init['nspid'])>=2 and init['nspid'][0]==init.get('pid') and init['nspid'][-1]==1,'readiness_private_PID1_required')
  need(init.get('ppid')==process.get('pid') and init.get('pgid')==process.get('pgid') and init.get('sid')==process.get('sid') and len({init.get('pid'),process.get('pid'),birth.get('pid')})==3,'readiness_parent_binding_changed')
  need(init.get('boot_id')==process.get('boot_id')==birth.get('boot_id') and init.get('uid')==process.get('uid')==birth.get('uid')==os.getuid(),'readiness_owner_changed')
  need(all(strict_birth_observation(v)['ended'] is False for v in (init,process,birth)),'readiness_exact_owner_not_live')
  snapshot_files=Workspace(target/'input').files()
  need(files_manifest(snapshot_files)==expected_workspace_sha256 and files_manifest(self.workspace.files())==expected_workspace_sha256,'readiness_artifact_version_changed')
  need(spec.get('environment_path')==(str(self.environment(spec.get('environment_id'))) if spec.get('environment_id') else None),'readiness_environment_changed')
  return dict(service_id=key,path=spec['path'],file_sha256=digest(snapshot_files[spec['path']]),workspace_manifest_sha256=expected_workspace_sha256,environment_id=spec.get('environment_id'),worker_birth=birth,init_birth=init,namespace_process_birth=process,pid_namespace_inode=inode,expires_at=spec['created_at']+spec['lifetime_seconds'])
 def owned_starting(self,key,expected_workspace_sha256,process):
  proof=self.readiness_identity(key,expected_workspace_sha256)
  need(process.returncode is None and proof['worker_birth']['pid']==process.pid,'readiness_spawn_owner_changed')
  return dict(ok=True,service_id=key,path=proof['path'],status='starting',health={},outcome_verified=False,owned_namespace_closed=False,workspace_manifest_sha256=expected_workspace_sha256,environment_id=proof['environment_id'],namespace_local_only=True,host_listening_port=False,expires_at=proof['expires_at'],automatic_retry=False,background_ready=True,owned_starting=True,readiness_owner_sha256=digest(json.dumps(proof,sort_keys=True,separators=(',',':')).encode()))
 async def start(self,path,check,environment_id=None,lifetime_seconds=300,expected_workspace_sha256=None,*,background_ready=False):
  need(type(background_ready) is bool,'background_ready_boolean_required');need(self.available(),'isolated_service_unavailable');relative(path);need(path.endswith('.py'),'Python_service_entrypoint_required');health(check)
  need(type(lifetime_seconds) is int and 10<=lifetime_seconds<=600,'bounded_service_lifetime_required')
  env=self.environment(environment_id);files=self.workspace.files();need(path in files,'service_entrypoint_missing');need(isinstance(expected_workspace_sha256,str) and expected_workspace_sha256==files_manifest(files),'fresh_workspace_manifest_required')
  services=folder(folder(self.root)/'services');need(len(list(services.iterdir()))<16,'service_archive_bound')
  # At most two live owned workers; stale states are not silently declared closed.
  live=0
  for old in services.iterdir():
   if old.is_dir():
    row=self.owner_loss(old,read(old/'spec.json')) or read(old/'state.json')
    live+=row.get('status') not in {'stopped','expired','failed'}
  need(live<2,'owned_service_concurrency_bound');key=uuid.uuid4().hex;target=services/key;need(len(os.fsencode(target/'control.sock'))<=107,'owned_service_socket_path_bound');target.mkdir(mode=0o700);snapshot=folder(target/'input')
  for name,raw in files.items():p=snapshot/name;p.parent.mkdir(parents=True,mode=0o700,exist_ok=True);p.write_bytes(raw)
  spec=dict(service_id=key,path=path,health=check,environment_id=environment_id,environment_path=str(env) if env else None,workspace_manifest_sha256=files_manifest(files),lifetime_seconds=lifetime_seconds,created_at=time.time(),token=uuid.uuid4().hex)
  save(target/'spec.json',spec);save(target/'state.json',dict(service_id=key,status='starting',outcome_verified=False))
  # Trusted fixed module only; the worker sees no model-supplied command string.
  envvars=dict(PATH='/usr/bin:/bin',LANG='C.UTF-8',PYTHONPATH=str(Path(inspect.getfile(Workspace)).resolve().parents[1]),PYTHONDONTWRITEBYTECODE='1')
  with (target/'supervisor.log').open('xb') as log:
   process=await asyncio.create_subprocess_exec(sys.executable,'-P','-B',str(Path(__file__).resolve()),'--worker',str(target),stdin=subprocess.DEVNULL,stdout=log,stderr=log,env=envvars,start_new_session=True)
  try:
   for _ in range(100):
    if (target/'control.sock').exists():
     if background_ready:
      try:owner=await asyncio.to_thread(read,target/'namespace-owner.json')
      except FileNotFoundError:await asyncio.sleep(.05);continue
      if owner.get('released') is not True:
       if process.returncode is not None:break
       await asyncio.sleep(.05);continue
      return await asyncio.to_thread(self.owned_starting,key,spec['workspace_manifest_sha256'],process)
     result=await self.status(key)
     if result.get('status') in {'running','failed','unverified','unhealthy'}:return result
    if process.returncode is not None:break
    await asyncio.sleep(.05)
   if background_ready:raise WorkspaceError('owned_background_start_transfer_unverified')
   return await self.status(key)
  except asyncio.CancelledError:
   # Only our freshly-created supervisor, never a process found by a PID string.
   if process.returncode is None:process.terminate();await process.wait()
   raise
  except BaseException:
   if background_ready and process.returncode is None:
    process.terminate()
    async with asyncio.timeout(6):await process.wait()
   raise
 async def view(self,service_id,expected_workspace_sha256,*,method='GET',path='/',content_type=None,body=b''):
  """Host-only view of one live private service; never a model RPC/tool."""
  identifier(service_id);need(type(body) is bytes and len(body)<=VIEW_BODY_MAX,'runtime_view_body_bound')
  try:request=view_request(dict(method=method,path=path,content_type=content_type,body_base64=base64.b64encode(body).decode()))
  except (ValueError,TypeError,UnicodeError):raise WorkspaceError('invalid_runtime_view_request') from None
  target=self.service_dir(service_id);spec=read(target/'spec.json')
  need(spec['service_id']==service_id and type(expected_workspace_sha256) is str and re.fullmatch('[0-9a-f]{64}',expected_workspace_sha256) and spec['workspace_manifest_sha256']==expected_workspace_sha256,'runtime_view_snapshot_mismatch')
  live=await self.status(service_id)
  need(live.get('status')=='running' and live.get('outcome_verified') is True and live.get('owned_namespace_closed') is False and live.get('workspace_manifest_sha256')==expected_workspace_sha256,'runtime_view_live_service_required')
  owner=read(target/'namespace-owner.json')
  need(owner.get('service_id')==service_id and owner.get('capture_before_artifact_release') is True and owner.get('released') is True and owner.get('closed') is False,'runtime_view_namespace_unverified')
  sock=target/'control.sock';need(stat.S_ISSOCK(sock.lstat().st_mode) and sock.stat().st_uid==os.getuid() and sock.stat().st_mode&0o077==0,'private_owned_service_socket_required')
  reader,writer=await asyncio.wait_for(asyncio.open_unix_connection(sock,limit=VIEW_WIRE_MAX+1),2)
  try:
   wire=(json.dumps(dict(operation='view',token=spec['token'],expected_workspace_sha256=expected_workspace_sha256,request=request))+'\n').encode()
   need(len(wire)<=VIEW_REQUEST_MAX,'runtime_view_request_bound');writer.write(wire);await writer.drain()
   raw=await asyncio.wait_for(reader.readline(),4);need(len(raw)<=VIEW_WIRE_MAX,'runtime_view_response_bound');value=json.loads(raw)
   need(time.time()<spec['created_at']+spec['lifetime_seconds'],'runtime_view_expired')
   keys={'ok','status','service_id','path','workspace_manifest_sha256','environment_id','request_path','method','http_status','content_type','body_base64','body_bytes','body_sha256','pid_namespace_inode','init_birth','namespace_local_only','host_listening_port','automatic_retry','outcome_verified','owned_namespace_closed'}
   need(type(value) is dict and set(value)==keys and value['ok'] is True and value['status']=='view','runtime_view_unavailable')
   need(value['service_id']==service_id and value['path']==spec['path'] and value['workspace_manifest_sha256']==expected_workspace_sha256 and value['environment_id']==spec.get('environment_id') and value['request_path']==path and value['method']==method,'runtime_view_binding_mismatch')
   need(value['pid_namespace_inode']==owner['pid_namespace_inode'] and value['init_birth']==owner['init'] and value['namespace_local_only'] is True and value['host_listening_port'] is False and value['automatic_retry'] is False and value['outcome_verified'] is True and value['owned_namespace_closed'] is False,'runtime_view_namespace_mismatch')
   need(type(value['http_status']) is int and 200<=value['http_status']<=599 and not 300<=value['http_status']<400 and value['content_type'] in VIEW_TYPES,'runtime_view_HTTP_response_refused')
   need(type(value['body_base64']) is str and len(value['body_base64'])<=87384,'runtime_view_response_bound')
   decoded=base64.b64decode(value['body_base64'],validate=True)
   need(len(decoded)<=VIEW_RESPONSE_MAX and type(value['body_bytes']) is int and len(decoded)==value['body_bytes'] and digest(decoded)==value['body_sha256'] and base64.b64encode(decoded).decode()==value['body_base64'],'runtime_view_body_mismatch')
   return value
  finally:writer.close();await writer.wait_closed()
 async def request(self,key,operation):
  target=self.service_dir(key);spec=read(target/'spec.json');need(spec['service_id']==key,'owned_service_identity_mismatch')
  def observation():
   state=self.owner_loss(target,spec,reconcile=True) or read(target/'state.json');need(isinstance(state,dict) and state.get('service_id')==key,'owned_service_state_identity_mismatch')
   need(state.get('workspace_manifest_sha256') in (None,spec['workspace_manifest_sha256']),'owned_service_state_manifest_mismatch')
   terminal=(state.get('status') in {'stopped','expired','failed'} and state.get('owned_namespace_closed') is True and state.get('outcome_verified') is True and state.get('workspace_manifest_sha256')==spec['workspace_manifest_sha256'] and state.get('environment_id')==spec.get('environment_id'))
   return state,terminal
  state,terminal=observation()
  def unverified():
   value=dict(ok=False,service_id=key,status='unverified',outcome_verified=False,owned_namespace_closed=None,
    workspace_manifest_sha256=spec['workspace_manifest_sha256'],environment_id=spec.get('environment_id'),automatic_retry=False)
   if spec.get('path') is not None:value['path']=spec['path']
   return value
  # Cleanup is durably committed before the worker removes its control socket.
  # A stale socket cannot overrule that verified receipt or authorize a new run.
  if terminal:return {**state,**({'path':spec['path']} if spec.get('path') is not None else {})}
  sock=target/'control.sock'
  if not sock.exists():
   return unverified()
  need(stat.S_ISSOCK(sock.lstat().st_mode) and sock.stat().st_uid==os.getuid() and sock.stat().st_mode&0o077==0,'private_owned_service_socket_required')
  try:
   reader,writer=await asyncio.wait_for(asyncio.open_unix_connection(sock),2)
   try:
    writer.write((json.dumps(dict(operation=operation,token=spec['token']))+'\n').encode());await writer.drain();raw=await asyncio.wait_for(reader.readline(),5);need(len(raw)<=8192,'service_response_bound');value=json.loads(raw);need(value['service_id']==key,'owned_service_response_mismatch')
    need(value.get('workspace_manifest_sha256')==spec['workspace_manifest_sha256'] and value.get('environment_id')==spec.get('environment_id'),'owned_service_response_snapshot_mismatch')
    if spec.get('path') is not None:value['path']=spec['path']
    return value
   finally:writer.close();await writer.wait_closed()
  except (OSError,TimeoutError):
   state,terminal=observation()
   if terminal:return {**state,**({'path':spec['path']} if spec.get('path') is not None else {})}
   return unverified()
 async def status(self,key):return await self.request(key,'status')
 async def stop(self,key):return await self.request(key,'stop')
 async def restart(self,key,expected_workspace_sha256=None):
  need(expected_workspace_sha256==files_manifest(self.workspace.files()),'fresh_workspace_manifest_required')
  old=read(self.service_dir(key)/'spec.json');stopped=await self.stop(key);need(stopped.get('status') in {'stopped','expired','failed'} and stopped.get('owned_namespace_closed') is True,'verified_stop_required_before_restart')
  result=await self.start(old['path'],old['health'],old['environment_id'],old['lifetime_seconds'],expected_workspace_sha256);return {**result,'replaces_service_id':key,'source_snapshot_refreshed':True,'automatic_retry':False}
 def capabilities(self):
  try:pins=sorted(self.catalogue());catalogue_available=True
  except (OSError,WorkspaceError):pins=[];catalogue_available=False
  services=[];path=self.root/'services';current_manifest=files_manifest(self.workspace.files())
  if path.exists():
   folder(path)
   for item in sorted(path.iterdir()):
    identifier(item.name);spec=read(item/'spec.json');row=self.owner_loss(item,spec) or read(item/'state.json');need(spec['service_id']==item.name,'owned_service_identity_mismatch')
    services.append(service_observation(spec,row,fresh=False,current_manifest=current_manifest))
  active=self.root/'active-environment.json';env=read(active)['environment_id'] if active.exists() else None
  if env:self.environment(env)
  return dict(ok=True,status='capabilities',isolated_service_available=self.available(),approved_catalogue_available=catalogue_available,approved_package_pins=pins,active_environment_id=env,workspace_manifest_sha256=current_manifest,services=services,network=False,maximum_lifetime_seconds=600,automatic_retry=False)
 async def inspect_capabilities(self):
  value=self.capabilities()
  async def current(row):
   if row['observation']=='durable_terminal_receipt':return row
   key=row['service_id'];spec=read(self.service_dir(key)/'spec.json')
   observed=await self.status(key)
   return service_observation(spec,observed,fresh=True,current_manifest=value['workspace_manifest_sha256'])
  # At most16 archived IDs; bounded socket observations only, no starts/stops.
  value['services']=await asyncio.gather(*(current(row) for row in value['services']))
  return value
 async def dispatch(self,command,**args):
  permitted={'capabilities':set(),'install':{'pins'},'start':{'path','health_check','environment_id','lifetime_seconds','expected_workspace_sha256','background_ready'},'status':{'service_id'},'stop':{'service_id'},'restart':{'service_id','expected_workspace_sha256'}}
  need(command in permitted,'runtime_command_not_permitted');args={k:v for k,v in args.items() if v is not None};need(set(args)<=permitted[command],'runtime_command_field_mismatch')
  if command=='capabilities':result=await self.inspect_capabilities()
  elif command=='install':result=self.install(args.get('pins'))
  elif command=='start':
   need('path' in args and 'health_check' in args,'runtime_service_parameters_required');result=await self.start(args['path'],args['health_check'],args.get('environment_id'),args.get('lifetime_seconds',300),args.get('expected_workspace_sha256'),background_ready=args.get('background_ready',False))
  elif command=='restart':result=await self.restart(identifier(args.get('service_id')),args.get('expected_workspace_sha256'))
  else:result=await getattr(self,command)(identifier(args.get('service_id')))
  result={**result,'command':command,'automatic_retry':False}
  if 'path' in args:result['path']=args['path']
  return result


class OwnedReadinessWatch:
 """One host-owned, read-only readiness observation after committed startup.

  No speech, model, launch, reconnect or implicit restart. Voice Stop cancels
  this watch only; the committed service keeps its original lifetime. A proved
  startup failure may request one exact-owned stop, never a guessed-PID signal.
 """
 def __init__(self,runtime,*,current,publish,before_cleanup=None,clock=time.monotonic,sleep=asyncio.sleep):
  need(callable(current) and callable(publish),'readiness_callbacks_required')
  self.runtime=runtime;self.current=current;self.publish=publish;self.clock=clock;self.sleep=sleep;self.before_cleanup=before_cleanup
  self.task=None;self.receipt=None;self.binding=None;self.closed=False;self.samples=0;self.stop_attempted=False
 async def begin(self,result,*,conversation_id,source_revision,goal_id,origin_generation,task_revision):
  need(not self.closed and self.task is None and self.current(),'readiness_one_current_watch_required')
  need(result.get('background_ready') is True and result.get('owned_starting') is True and result.get('status')=='starting' and result.get('outcome_verified') is False,'readiness_owned_starting_not_ready_required')
  need(all(isinstance(v,str) and re.fullmatch('[0-9a-f]{32}' if k!='source' else '[0-9a-f]{64}',v) for k,v in (('conversation',conversation_id),('source',source_revision),('goal',goal_id))) and type(origin_generation) is int and origin_generation>=0 and type(task_revision) is int and task_revision>0,'readiness_host_identity_required')
  proof=await asyncio.to_thread(self.runtime.readiness_identity,result['service_id'],result['workspace_manifest_sha256'])
  need(self.current() and digest(json.dumps(proof,sort_keys=True,separators=(',',':')).encode())==result.get('readiness_owner_sha256'),'readiness_start_transfer_identity_changed')
  self.binding=dict(conversation_id=conversation_id,source_revision=source_revision,goal_id=goal_id,task_revision=task_revision,origin_generation=origin_generation,owner=proof,owner_sha256=result['readiness_owner_sha256'])
  self.task=asyncio.create_task(self.run());return dict(status='readiness_watch_started',service_id=proof['service_id'],readiness=False,backend_start_transferred=True,automatic_retry=False)
 async def run(self):
  bound=self.binding;owner=bound['owner'];deadline=self.clock()+10;kind='not_ready';observed=None;cleanup=None
  try:
   while self.current() and not self.closed and self.clock()<deadline and self.samples<20:
    proof=await asyncio.to_thread(self.runtime.readiness_identity,owner['service_id'],owner['workspace_manifest_sha256'])
    need(self.current() and not self.closed and proof==owner,'readiness_source_task_artifact_owner_changed')
    async with asyncio.timeout(min(6,max(.001,deadline-self.clock()))):observed=await self.runtime.status(owner['service_id'])
    self.samples+=1
    need(self.current() and not self.closed,'readiness_session_no_longer_current')
    need(observed.get('service_id')==owner['service_id'] and observed.get('workspace_manifest_sha256')==owner['workspace_manifest_sha256'] and observed.get('environment_id')==owner['environment_id'],'readiness_status_identity_changed')
    if observed.get('status')=='running':
     need(observed.get('outcome_verified') is True and observed.get('owned_namespace_closed') is False and observed.get('health',{}).get('healthy') is True,'readiness_fresh_health_required')
     final=await asyncio.to_thread(self.runtime.readiness_identity,owner['service_id'],owner['workspace_manifest_sha256']);need(final==owner and self.current() and not self.closed,'readiness_final_binding_changed')
     kind='ready';break
    if observed.get('status') in {'failed','unhealthy'}:kind='failed';break
    if observed.get('status')!='starting':kind='unverified';break
    await self.sleep(min(.5,max(0,deadline-self.clock())))
   if not self.current() or self.closed:return
   if kind=='failed' and not (observed.get('outcome_verified') is True and observed.get('owned_namespace_closed') is True):
    # Deadline/unknown status never grants shutdown. Only a fresh explicit
    # failed/unhealthy startup is rechecked before its exact-owned rollback.
    proof=await asyncio.to_thread(self.runtime.readiness_identity,owner['service_id'],owner['workspace_manifest_sha256']);need(proof==owner and self.current() and not self.closed,'readiness_cleanup_owner_changed')
    async with asyncio.timeout(min(6,max(.001,deadline-self.clock()))):observed=await self.runtime.status(owner['service_id'])
    need(self.current() and not self.closed and observed.get('service_id')==owner['service_id'] and observed.get('workspace_manifest_sha256')==owner['workspace_manifest_sha256'] and observed.get('environment_id')==owner['environment_id'],'readiness_fresh_rollback_readback_changed')
    if observed.get('status')=='running':
     need(observed.get('outcome_verified') is True and observed.get('owned_namespace_closed') is False and observed.get('health',{}).get('healthy') is True,'readiness_fresh_health_required')
     proof=await asyncio.to_thread(self.runtime.readiness_identity,owner['service_id'],owner['workspace_manifest_sha256']);need(proof==owner and self.current() and not self.closed,'readiness_ready_race_binding_changed');kind='ready'
    elif observed.get('status') in {'failed','unhealthy'}:
     if not (observed.get('outcome_verified') is True and observed.get('owned_namespace_closed') is True):
      need(callable(self.before_cleanup),'readiness_cleanup_authority_unavailable');await self.before_cleanup(owner)
      proof=await asyncio.to_thread(self.runtime.readiness_identity,owner['service_id'],owner['workspace_manifest_sha256']);need(proof==owner and self.current() and not self.closed,'readiness_rollback_owner_changed')
      self.stop_attempted=True;cleanup=await self.stop_failed_once(owner['service_id'])
      need(cleanup.get('service_id')==owner['service_id'] and cleanup.get('workspace_manifest_sha256')==owner['workspace_manifest_sha256'] and cleanup.get('outcome_verified') is True and cleanup.get('owned_namespace_closed') is True,'readiness_failed_start_cleanup_unverified')
    else:kind='unverified'
   if not self.current() or self.closed:return
   self.receipt=dict(kind='service_ready' if kind=='ready' else 'service_startup_failed' if kind=='failed' else 'service_readiness_unverified',**{k:v for k,v in bound.items() if k!='owner'},service_id=owner['service_id'],path=owner['path'],file_sha256=owner['file_sha256'],workspace_manifest_sha256=owner['workspace_manifest_sha256'],fresh_health=observed,cleanup=cleanup,verified=kind in {'ready','failed'},readiness=kind=='ready',preview_opened=False,action_authority=False,automatic_retry=False,samples=self.samples,watch_outcome=kind,backend_unchanged=not self.stop_attempted,ready_offer_allowed=kind=='ready')
   await self.publish(dict(self.receipt))
  except asyncio.CancelledError:raise
  except Exception as exc:
   self.receipt=dict(kind='service_readiness_unverified',conversation_id=bound['conversation_id'],source_revision=bound['source_revision'],goal_id=bound['goal_id'],service_id=owner['service_id'],verified=False,error_type=type(exc).__name__,automatic_retry=False,stop_attempted=self.stop_attempted,ready_offer_allowed=False,success_speech_allowed=False,backend_unchanged=not self.stop_attempted)
   if self.current() and not self.closed:await self.publish(dict(self.receipt))
 async def stop_failed_once(self,key):
  # A cleanup request already sent is settled rather than canceled/replayed by
  # a simultaneous voice Stop. Its exact target and original6s bound remain.
  task=asyncio.create_task(self.runtime.stop(key));deadline=asyncio.get_running_loop().time()+6;cancelled=False
  while not task.done():
   try:
    done,_=await asyncio.wait({task},timeout=max(0,deadline-asyncio.get_running_loop().time()))
    if not done:
     task.cancel();await asyncio.gather(task,return_exceptions=True)
     raise WorkspaceError('readiness_cleanup_timeout_unverified')
   except asyncio.CancelledError:cancelled=True
  result=task.result()
  if cancelled:raise asyncio.CancelledError()
  return result
 async def close(self):
  self.closed=True
  if self.task is not None and not self.task.done():
   self.task.cancel();await asyncio.gather(self.task,return_exceptions=True)
  return dict(watch_closed=True,backend_stopped=False,automatic_retry=False)


def active_packages(runtime_root):
 """Return verified active install for a caller's read-only /packages mount."""
 root=Path(runtime_root);path=root/'active-environment.json'
 if not path.exists():return None
 key=read(path)['environment_id'];return Runtime(None,root,root/'unused-wheelhouse').environment(key)

def active_environment_sha256(runtime_root):
 site=active_packages(runtime_root)
 return read(site.parent/'receipt.json')['environment_sha256'] if site is not None else None

def _validate_runtime_result(value,arguments,expected_manifest=None):
 need(isinstance(value,dict) and isinstance(arguments,dict),'runtime_receipt_object_required');command=arguments.get('command')
 clean={k:v for k,v in value.items() if k not in {'generation','request_id','server_role','server_pid'}}
 allowed={'ok','command','status','service_id','environment_id','active_environment_id','path','packages','files_sha256','environment_sha256','network','install_hooks_executed','imports_verified','health','outcome_verified','owned_namespace_closed','workspace_manifest_sha256','namespace_local_only','host_listening_port','expires_at','automatic_retry','replaces_service_id','source_snapshot_refreshed','isolated_service_available','approved_catalogue_available','approved_package_pins','services','maximum_lifetime_seconds','background_ready','owned_starting','readiness_owner_sha256'}
 need(set(clean)<=allowed and clean.get('command')==command and type(clean.get('ok')) is bool and clean.get('automatic_retry') is False,'runtime_receipt_fields_mismatch')
 need(clean.get('status') in {'capabilities','installed_verified','starting','running','unhealthy','unverified','stopped','expired','failed'},'runtime_receipt_state_invalid')
 if command=='capabilities':
  need(clean['status']=='capabilities' and clean['ok'] is True and clean.get('network') is False and clean.get('maximum_lifetime_seconds')==600 and type(clean.get('isolated_service_available')) is bool and type(clean.get('approved_catalogue_available')) is bool,'runtime_capability_receipt_invalid')
  pins=clean.get('approved_package_pins');services=clean.get('services');need(isinstance(pins,list) and len(pins)<=32 and len(set(pins))==len(pins) and all(isinstance(p,str) and PIN.fullmatch(p) for p in pins),'runtime_capability_packages_invalid')
  need(isinstance(services,list) and len(services)<=16,'runtime_capability_services_invalid')
  for row in services:
   fields={'service_id','status','outcome_verified','observation','last_recorded_status','workspace_manifest_sha256','current_workspace_matches'}
   need(isinstance(row,dict) and set(row) in (fields,fields|{'path'}) and type(row['outcome_verified']) is bool and type(row['current_workspace_matches']) is bool
    and row['status'] in {'unverified','running','stopped','expired','failed'} and row['last_recorded_status'] in {'starting','running','unhealthy','unverified','stopped','expired','failed'}
    and row['observation'] in {'cached_record_not_live','fresh_status','durable_terminal_receipt'} and re.fullmatch('[0-9a-f]{64}',row['workspace_manifest_sha256']),
    'runtime_capability_service_invalid');identifier(row['service_id'])
   if 'path' in row:relative(row['path'])
   need(not(row['status']=='running' and (row['observation']!='fresh_status' or not row['outcome_verified'])),'runtime_cached_state_cannot_prove_liveness')
  if clean.get('active_environment_id') is not None:identifier(clean['active_environment_id'])
  need(re.fullmatch('[0-9a-f]{64}',clean.get('workspace_manifest_sha256','')),'runtime_capability_manifest_invalid')
 if command=='install':
  identifier(clean.get('environment_id'));need(clean.get('status')=='installed_verified' and clean.get('network') is False and clean.get('install_hooks_executed') is False and clean.get('imports_verified') is False and re.fullmatch('[0-9a-f]{64}',clean.get('environment_sha256','')),'runtime_install_receipt_invalid')
  pins=clean.get('packages');requested=arguments.get('pins');need(isinstance(pins,list) and 1<=len(pins)<=8 and all(isinstance(p,str) and PIN.fullmatch(p) for p in pins) and len(set(pins))==len(pins) and isinstance(requested,list) and all(normalized(p.split('==')[0])+'=='+p.split('==')[1] in pins for p in requested),'runtime_installed_packages_mismatch')
 if command in {'start','status','stop','restart'}:
  identifier(clean.get('service_id'))
  if command in {'status','stop'}:need(clean['service_id']==arguments.get('service_id'),'runtime_service_identity_mismatch')
  if command=='restart':need(clean.get('replaces_service_id')==arguments.get('service_id') and clean.get('source_snapshot_refreshed') is True,'runtime_restart_identity_mismatch')
  if command=='start':need(clean.get('path')==arguments.get('path'),'runtime_service_path_mismatch')
  if command in {'start','restart'}:need(clean.get('workspace_manifest_sha256')==arguments.get('expected_workspace_sha256') and (expected_manifest is None or clean['workspace_manifest_sha256']==expected_manifest),'runtime_service_manifest_mismatch')
  if any(k in clean for k in ('background_ready','owned_starting','readiness_owner_sha256')):
   need(command=='start' and arguments.get('background_ready') is True and clean['status']=='starting' and clean.get('background_ready') is True and clean.get('owned_starting') is True and clean.get('outcome_verified') is False and clean.get('owned_namespace_closed') is False and re.fullmatch('[0-9a-f]{64}',clean.get('readiness_owner_sha256','')),'runtime_owned_starting_receipt_invalid')
  if clean['status']=='running':need(clean.get('outcome_verified') is True and clean.get('health',{}).get('healthy') is True and clean.get('namespace_local_only') is True and clean.get('host_listening_port') is False,'runtime_health_proof_required')
  if clean['status']=='unverified':need(clean['ok'] is False and clean.get('outcome_verified') is False,'runtime_unverified_result_cannot_claim_success')
  else:
   need(type(clean.get('owned_namespace_closed')) is bool and type(clean.get('outcome_verified')) is bool and clean.get('namespace_local_only') is True and clean.get('host_listening_port') is False and type(clean.get('expires_at')) in (int,float) and math.isfinite(clean['expires_at']),'runtime_service_bounds_invalid')
   proof=clean.get('health');need(isinstance(proof,dict) and set(proof)<={'healthy','http_status','response_sha256','service_exit_code','error','error_type'},'runtime_health_fields_invalid')
   if proof.get('healthy') is True:
    need(type(proof.get('http_status')) is int and 100<=proof['http_status']<=599 and re.fullmatch('[0-9a-f]{64}',proof.get('response_sha256','')),'runtime_health_response_invalid')
    if command=='start':need(proof['http_status']==arguments['health_check'].get('status',200),'runtime_health_status_mismatch')
  if clean.get('outcome_verified') is True and clean['status'] in {'stopped','expired','failed'}:need(clean.get('owned_namespace_closed') is True,'runtime_cleanup_proof_required')
 return clean

def validate_runtime_result(value,arguments,expected_manifest=None):
 try:return _validate_runtime_result(value,arguments,expected_manifest)
 except WorkspaceError:raise
 except (KeyError,TypeError,ValueError,AttributeError,IndexError):raise WorkspaceError('runtime_receipt_invalid') from None

def namespace_main():
 """Only this stdlib function enters the networkless service namespace."""
 import hashlib,http.client,json,os,resource,runpy,signal,subprocess,sys,time,base64
 resource.setrlimit(resource.RLIMIT_CPU,(60,60));resource.setrlimit(resource.RLIMIT_AS,(268435456,268435456));resource.setrlimit(resource.RLIMIT_FSIZE,(1048576,1048576));resource.setrlimit(resource.RLIMIT_NOFILE,(64,64));resource.setrlimit(resource.RLIMIT_NPROC,(16,16))
 path=sys.argv[1];check=json.loads(sys.argv[2]);os.close(int(sys.argv[3]));port=8765;namespace_inode=os.stat('/proc/self/ns/pid').st_ino
 child="import runpy,sys;sys.path[:0]=['/workspace','/packages'];runpy.run_path(sys.argv[1],run_name='__main__')"
 logs=open('/tmp/service.log','wb');app=subprocess.Popen([sys.executable,'-I','-B','-c',child,'/workspace/'+path],stdin=subprocess.DEVNULL,stdout=logs,stderr=logs,env=dict(PATH='/usr/bin',HOME='/tmp',LANG='C.UTF-8',ME3_SERVICE_PORT=str(port)),cwd='/workspace')
 def status():
  if app.poll() is not None:return dict(healthy=False,service_exit_code=app.returncode,error='service_exited')
  conn=http.client.HTTPConnection('127.0.0.1',port,timeout=.5)
  try:
   conn.request('GET',check['path'],headers={'Connection':'close'});response=conn.getresponse();body=response.read(65537)
   matched=len(body)<=65536 and response.status==check['status'] and check['contains'] in body.decode('utf-8','replace')
   proof=dict(healthy=matched,http_status=response.status,response_sha256=hashlib.sha256(body).hexdigest(),service_exit_code=None)
   if len(body)>65536:proof['error']='service_health_response_bound'
   return proof
  except OSError:return dict(healthy=False,error='service_health_unavailable',service_exit_code=app.poll())
  finally:conn.close()
 def view(request):
  try:
   request=view_request(request)
   if app.poll() is not None:raise ValueError('service_exited')
   conn=http.client.HTTPConnection('127.0.0.1',port,timeout=1)
   def expired(*unused):raise TimeoutError('private_view_deadline')
   previous=signal.signal(signal.SIGALRM,expired);signal.setitimer(signal.ITIMER_REAL,1.25)
   try:
    headers={'Connection':'close'}
    if request['method']=='POST':headers['Content-Type']=request['content_type']
    conn.request(request['method'],request['path'],body=base64.b64decode(request['body_base64'],validate=True),headers=headers)
    response=conn.getresponse();kind=response.getheader('Content-Type','').split(';',1)[0].strip().lower()
    if not 200<=response.status<=599 or 300<=response.status<400 or kind not in VIEW_TYPES:raise ValueError('HTTP_response_refused')
    length=response.getheader('Content-Length')
    if length is not None and (not length.isdecimal() or int(length)>65536):raise ValueError('HTTP_response_bound')
    body=response.read(65537)
    if len(body)>65536:raise ValueError('HTTP_response_bound')
    return dict(ok=True,http_status=response.status,content_type=kind,body_base64=base64.b64encode(body).decode(),body_bytes=len(body),body_sha256=hashlib.sha256(body).hexdigest())
   finally:signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,previous);conn.close()
  except (OSError,ValueError,http.client.HTTPException):return dict(ok=False,error='namespace_view_refused')
 try:
  for line in sys.stdin:
   request=json.loads(line)
   if request=={'operation':'stop'}:break
   value=view(request['request']) if set(request)=={'operation','request'} and request['operation']=='view' else status() if request=={'operation':'status'} else dict(ok=False,error='namespace_command_refused')
   print(json.dumps({**value,'_namespace_pid_inode':namespace_inode}),flush=True)
 finally:
  if app.poll() is None:
   app.terminate()
   try:app.wait(timeout=.5)
   except subprocess.TimeoutExpired:app.kill();app.wait(timeout=1)
  logs.close()

def service_command(snapshot,environment,path,check,*,info_fd,block_fd,guard_fd):
 need(sys.platform=='linux' and Path('/usr/bin/bwrap').is_file(),'isolated_service_unavailable')
 args=['/usr/bin/bwrap','--info-fd',str(info_fd),'--block-fd',str(block_fd),'--unshare-all','--unshare-user','--die-with-parent','--new-session','--cap-drop','ALL','--clearenv','--setenv','PATH','/usr/bin','--setenv','HOME','/tmp','--setenv','LANG','C.UTF-8']
 for p in ['/usr','/lib','/lib64','/bin']:
  if Path(p).exists():args+=['--ro-bind',p,p]
 args+=['--proc','/proc','--dev','/dev','--remount-ro','/dev','--size','8388608','--tmpfs','/tmp','--ro-bind',str(snapshot),'/workspace']
 args+=['--ro-bind',str(environment),'/packages'] if environment else ['--dir','/packages']
 args+=['--chdir','/workspace','--remount-ro','/','--','/usr/bin/python3','-I','-B','-c',inspect.getsource(view_request)+'\nVIEW_TYPES='+repr(VIEW_TYPES)+'\n'+inspect.getsource(namespace_main)+'\nnamespace_main()\n',path,json.dumps(check),str(guard_fd)]
 return args

def worker(target):
 """Host-side bounded owner; artifact code runs only in its child namespace."""
 import select
 target=folder(target);spec=read(target/'spec.json');identifier(spec['service_id']);health(spec['health']);relative(spec['path']);need(target.name==spec['service_id'],'service_worker_identity_mismatch')
 need(files_manifest(Workspace(target/'input').files())==spec['workspace_manifest_sha256'],'service_snapshot_changed')
 address=target/'control.sock';need(not address.exists(),'service_socket_already_exists')
 listener=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);listener.bind(str(address));os.chmod(address,0o600);listener.listen(2);listener.settimeout(.1)
 process=None;owner=None;birth=None;namespace_inode=None;cleanup_started=False;ending='failed';closed=False;last={};started=time.monotonic();lifetime=min(spec['lifetime_seconds'],max(0,spec['created_at']+spec['lifetime_seconds']-time.time()))
 save(target/'worker-owner.json',dict(service_id=spec['service_id'],worker=process_birth(os.getpid()),snapshot=str(target/'input')))
 def interrupted(*_):raise InterruptedError()
 signal.signal(signal.SIGTERM,interrupted);signal.signal(signal.SIGINT,interrupted)
 def state(status,proof=None):
  if status=='unverified':proof=dict(healthy=False,error='owned_service_cleanup_unverified')
  verified=(status=='running' and bool((proof or {}).get('healthy'))) or (status in {'stopped','expired','failed'} and closed)
  value=dict(ok=status not in {'failed','unverified'},service_id=spec['service_id'],path=spec['path'],status=status,health=proof or {},outcome_verified=verified,owned_namespace_closed=closed,workspace_manifest_sha256=spec['workspace_manifest_sha256'],environment_id=spec['environment_id'],namespace_local_only=True,host_listening_port=False,expires_at=spec['created_at']+spec['lifetime_seconds'],automatic_retry=False)
  save(target/'state.json',value);return value
 def health_request():
  nonlocal namespace_inode
  if process.poll() is not None:return dict(healthy=False,error='service_namespace_exited')
  process.stdin.write(b'{"operation":"status"}\n');process.stdin.flush();ready,_,_=select.select([process.stdout],[],[],1)
  if not ready:return dict(healthy=False,error='service_health_timeout')
  raw=process.stdout.readline(8193);need(len(raw)<=8192,'service_health_response_bound');value=json.loads(raw)
  observed=value.pop('_namespace_pid_inode',None)
  need(owner is not None and owner.verified and type(observed) is int and observed==owner.info['pid-namespace'],'private_pid_namespace_required')
  need(namespace_inode in (None,observed),'owned_service_namespace_changed');namespace_inode=observed
  save(target/'namespace-owner.json',dict(service_id=spec['service_id'],**owner.receipt(),snapshot=str(target/'input')))
  return value
 def view_request_owned(request,expected):
  need(expected==spec['workspace_manifest_sha256'] and time.time()<spec['created_at']+spec['lifetime_seconds'],'runtime_view_snapshot_or_expiry_mismatch')
  need(owner is not None and owner.verified and not cleanup_started and not owner.closed and process.poll() is None,'runtime_view_live_namespace_required')
  request=view_request(request)
  process.stdin.write((json.dumps(dict(operation='view',request=request))+'\n').encode());process.stdin.flush()
  ready,_,_=select.select([process.stdout],[],[],2)
  need(ready,'runtime_view_pipe_timeout');raw=process.stdout.readline(VIEW_WIRE_MAX+1);need(len(raw)<=VIEW_WIRE_MAX,'runtime_view_response_bound');value=json.loads(raw)
  need(value.pop('_namespace_pid_inode',None)==owner.info['pid-namespace'] and value.get('ok') is True,'runtime_view_namespace_or_HTTP_unverified')
  return dict(**value,status='view',service_id=spec['service_id'],path=spec['path'],workspace_manifest_sha256=spec['workspace_manifest_sha256'],environment_id=spec.get('environment_id'),request_path=request['path'],method=request['method'],pid_namespace_inode=owner.info['pid-namespace'],init_birth=owner.birth,namespace_local_only=True,host_listening_port=False,automatic_retry=False,outcome_verified=True,owned_namespace_closed=False)
 def cleanup():
  nonlocal closed,cleanup_started
  if cleanup_started:return closed
  cleanup_started=True
  if owner is not None:
   closed=owner.close();save(target/'namespace-closure.json',dict(service_id=spec['service_id'],**owner.receipt()))
  return closed
 try:
  with (target/'namespace.log').open('xb') as errors:
   owner=BlockedNamespace(lambda info,block,guard:service_command(target/'input',Path(spec['environment_path']) if spec['environment_path'] else None,spec['path'],spec['health'],info_fd=info,block_fd=block,guard_fd=guard),errors)
   process=owner.process;birth=process_birth(process.pid)
   save(target/'namespace-owner.json',dict(service_id=spec['service_id'],**owner.receipt(),snapshot=str(target/'input')))
   owner.release()
   save(target/'namespace-owner.json',dict(service_id=spec['service_id'],**owner.receipt(),snapshot=str(target/'input')))
   state('starting')
   while time.monotonic()-started<lifetime:
    try:client,_=listener.accept()
    except socket.timeout:
     if process.poll() is not None:ending='failed';break
     continue
    with client:
     client.settimeout(2);raw=bytearray()
     while len(raw)<=VIEW_REQUEST_MAX and not raw.endswith(b'\n'):
      part=client.recv(1024)
      if not part:break
      raw.extend(part)
     need(len(raw)<=VIEW_REQUEST_MAX,'owned_service_request_bound');request=json.loads(raw)
     if request.get('operation')=='view':
      try:
       need(set(request)=={'operation','token','request','expected_workspace_sha256'} and request['token']==spec['token'],'owned_service_view_identity_required')
       value=view_request_owned(request['request'],request['expected_workspace_sha256'])
      except (WorkspaceError,ValueError,TypeError,KeyError):value=dict(ok=False,status='view_unavailable',automatic_retry=False)
      wire=(json.dumps(value)+'\n').encode();need(len(wire)<=VIEW_WIRE_MAX,'runtime_view_wire_bound')
      try:client.sendall(wire)
      except (BrokenPipeError,ConnectionResetError):pass # A dropped view does not stop the background service.
      continue
     need(len(raw)<=1024 and set(request)=={'operation','token'} and request['token']==spec['token'] and request['operation'] in {'status','stop'},'owned_service_command_required')
     if request['operation']=='stop':ending='stopped';cleanup();value=state(ending if closed else 'unverified');client.sendall((json.dumps(value)+'\n').encode());break
     last=health_request();value=state('running' if last.get('healthy') else 'starting' if time.monotonic()-started<5 else 'unhealthy',last);client.sendall((json.dumps(value)+'\n').encode())
   else:ending='expired'
 except (InterruptedError,KeyboardInterrupt):ending='stopped'
 except BaseException as exc:ending='failed';last=dict(error_type=type(exc).__name__)
 finally:
  cleanup()
  try:state(ending if closed else 'unverified',last)
  finally:
   listener.close()
   if address.exists():address.unlink()
   if owner is not None:owner.dispose()

if __name__=='__main__':
 import argparse
 parser=argparse.ArgumentParser(__doc__);parser.add_argument('--worker',type=Path,required=True);args=parser.parse_args();worker(args.worker)
