"""End only the attested ME3 WhatsApp browser, with independent local proof.

This proves local browser/session/audio termination, never a remote server's
acknowledgment. No directory/message content, broad process kill or defaults.
"""
import asyncio,hashlib,json,os,re,secrets,sqlite3,stat,time
from contextlib import contextmanager,asynccontextmanager,closing
from pathlib import Path
from urllib.parse import urlsplit
from .backend import process_arguments
from .guard import Rejected
from me3_assistant.guard import lock
from me3_stream.browser import CDP
import aiohttp

ORIGIN='https://web.whatsapp.com'
def ident(v):
 if not isinstance(v,str) or not re.fullmatch('[a-f0-9]{32}',v):raise Rejected('native_call_identity_required')
 return v
def process_row(pid):
 try:
  p=Path('/proc')/str(pid);raw=(p/'stat').read_text();tail=raw[raw.rfind(')')+2:].split();args=process_arguments((p/'cmdline').read_bytes())
  return dict(pid=int(pid),parent=int(tail[1]),group=int(tail[2]),session=int(tail[3]),start=int(tail[19]),uid=p.stat().st_uid,exe=(p/'exe').resolve().name,args=args)
 except (OSError,ValueError,IndexError,UnicodeError):return None
def processes():return [r for p in Path('/proc').glob('[0-9]*') if (r:=process_row(p.name)) is not None]
def digest(v):return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def owned_graph_environment(environ,uid,lstat=os.lstat):
 # This environment belongs only to one read-only pw-dump subprocess. The
 # minimal MCP parent intentionally has no desktop/audio session variables.
 if type(uid) is not int or uid<0:raise Rejected('native_audio_runtime_identity_required')
 runtime='/run/user/'+str(uid);socket=runtime+'/pipewire-0'
 for p in ('/run','/run/user'):
  s=lstat(p)
  if not stat.S_ISDIR(s.st_mode) or s.st_uid!=0 or s.st_mode&0o022:raise Rejected('native_audio_runtime_parent_required')
 s=lstat(runtime)
 if not stat.S_ISDIR(s.st_mode) or s.st_uid!=uid or stat.S_IMODE(s.st_mode)!=0o700:raise Rejected('native_audio_private_runtime_required')
 s=lstat(socket)
 if not stat.S_ISSOCK(s.st_mode) or s.st_uid!=uid:raise Rejected('native_audio_owned_socket_required')
 if (any(environ.get(k) not in (None,runtime) for k in ('XDG_RUNTIME_DIR','PIPEWIRE_RUNTIME_DIR'))
     or environ.get('PIPEWIRE_REMOTE') not in (None,'pipewire-0',socket)):
  raise Rejected('native_audio_runtime_mismatch')
 return dict(PATH='/usr/bin:/bin',LANG='C.UTF-8',XDG_RUNTIME_DIR=runtime,PIPEWIRE_RUNTIME_DIR=runtime,PIPEWIRE_REMOTE='pipewire-0')

async def owned_readonly_graph():
 env=owned_graph_environment(os.environ,os.getuid())
 process=await asyncio.create_subprocess_exec('/usr/bin/pw-dump',env=env,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
 async def read():
  output=bytearray()
  while True:
   chunk=await process.stdout.read(min(65536,2_000_001-len(output)))
   if not chunk:break
   output.extend(chunk)
   if len(output)>2_000_000:raise Rejected('native_audio_graph_size_limit')
  if await process.wait()!=0:raise Rejected('native_audio_graph_read_failed')
  try:value=json.loads(output)
  except (ValueError,UnicodeError):raise Rejected('native_audio_graph_invalid') from None
  if not isinstance(value,list):raise Rejected('native_audio_graph_invalid')
  return value
 try:return await asyncio.wait_for(read(),5)
 except BaseException:
  if process.returncode is None:process.kill()
  await asyncio.wait_for(process.wait(),1)
  raise

class NativeCallOwner:
 def __init__(self,root,path,backend,*,processes_fn=processes,graph_fn=None,pid_exists_fn=None):
  self.root=Path(root).resolve();self.path=Path(path);self.backend=backend;self.processes=processes_fn
  self.pid_exists=pid_exists_fn or (lambda pid:(Path('/proc')/str(pid)).exists())
  if self.path.is_symlink() or not self.path.resolve().is_relative_to(self.root):raise Rejected('private_native_call_store_required')
  if graph_fn is None:
   graph_fn=owned_readonly_graph
  self.graph=graph_fn;self.path.parent.mkdir(parents=True,exist_ok=True)
  fd=os.open(self.path,os.O_CREAT|os.O_RDWR|getattr(os,'O_NOFOLLOW',0),0o600);os.close(fd)
  with self.db() as c:
   c.execute('CREATE TABLE IF NOT EXISTS owners (operation TEXT PRIMARY KEY,owner TEXT,person TEXT,attestation TEXT,state TEXT)')
   c.execute('CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT,operation TEXT,state TEXT)')
 @contextmanager
 def db(self):
  with closing(sqlite3.connect(self.path,timeout=3)) as c:
   c.row_factory=sqlite3.Row
   try:c.execute('PRAGMA synchronous=FULL');c.execute('BEGIN IMMEDIATE');yield c;c.commit()
   except BaseException:c.rollback();raise
 def row(self,op,owner,person):
  ident(op);ident(owner)
  with self.db() as c:r=c.execute('SELECT * FROM owners WHERE operation=?',(op,)).fetchone()
  if r is None or r['owner']!=owner or r['person']!=person:raise Rejected('native_call_owner_binding_changed')
  v=dict(r);v['attestation']=json.loads(v['attestation']);return v
 def profile_roots(self,rows):
  profile=self.backend.profile();flag='--user-data-dir='+str(profile)
  return [r for r in rows if r['uid']==os.getuid() and r['exe']=='chromium' and flag in r['args'] and not any(a.startswith('--type=') for a in r['args'])]
 def attested_endpoint(self,attestation):
  self.backend.check();rows=self.processes();roots=self.profile_roots(rows);a=attestation
  if (len(roots)!=1 or any(roots[0][k]!=a[k] for k in ('pid','start','group','session'))
      or any(p['uid']!=os.getuid() for p in rows if p['session']==a['session'])):
   raise Rejected('exact_attested_browser_process_required')
  debug=self.backend.profile()/'DevToolsActivePort'
  if debug.is_symlink() or not debug.is_file():raise Rejected('owned_call_debug_binding_changed')
  raw=debug.read_bytes()
  if hashlib.sha256(raw).hexdigest()!=a['debug_sha256']:raise Rejected('owned_call_debug_binding_changed')
  lines=raw.decode().splitlines()
  if (len(lines)<2 or not lines[0].isdigit() or int(lines[0])!=a['port']
      or not 1024<=int(lines[0])<=65535
      or not re.fullmatch('/devtools/browser/[A-Za-z0-9_-]+',lines[1])):
   raise Rejected('exact_attested_browser_endpoint_required')
  return 'ws://127.0.0.1:'+str(a['port'])+lines[1]
 @asynccontextmanager
 async def connect_attested(self,op,owner,person):
  """Read-only browser-level reconnect for exact owned End/reconciliation.

  No start, navigation, header inspection, page-selection heuristics or close
  happens here. close_once still checks every page and reserves its one close.
  """
  with lock(self.root/'data/whatsapp-browser.lock'):
   r=self.row(op,owner,person)
   if self.backend.page is not None:raise Rejected('exclusive_native_end_connection_required')
   endpoint=self.attested_endpoint(r['attestation'])
   session=aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12),trust_env=False)
   page=None
   try:
    self.backend.check();ws=await session.ws_connect(endpoint,max_msg_size=100000)
    # A restart/port change during the handshake cannot acquire an old owner.
    if self.attested_endpoint(r['attestation'])!=endpoint:raise Rejected('attested_browser_changed_during_connect')
    page=CDP(ws);self.backend.page=page
    yield self.backend
   finally:
    if self.backend.page is page:self.backend.page=None
    await session.close()
 async def attest(self,op,owner,person,root_target):
  ident(op);ident(owner)
  if not isinstance(person,str) or not re.fullmatch('[a-f0-9]{64}',person):raise Rejected('exact_person_digest_required')
  self.backend.check();rows=self.processes();roots=self.profile_roots(rows)
  if len(roots)!=1:raise Rejected('unique_private_WhatsApp_browser_required')
  main=roots[0]
  if not main['pid']==main['group']==main['session']:raise Rejected('independent_owned_browser_session_required')
  group=[r for r in rows if r['session']==main['session']]
  if not group or any(r['uid']!=os.getuid() for r in group):raise Rejected('owned_browser_session_identity_required')
  p=self.backend.profile()/'DevToolsActivePort'
  if p.is_symlink() or not p.is_file():raise Rejected('owned_debug_binding_required')
  raw=p.read_bytes();lines=raw.decode().splitlines()
  if len(lines)<2 or not lines[0].isdigit() or not 1024<=int(lines[0])<=65535 or not re.fullmatch('/devtools/browser/[A-Za-z0-9_-]+',lines[1]):raise Rejected('owned_debug_binding_required')
  info=(await self.backend.page.call('Target.getTargetInfo')).get('targetInfo',{})
  if info.get('type')!='page' or info.get('targetId')!=root_target or urlsplit(info.get('url','')).scheme+'://'+urlsplit(info.get('url','')).netloc!=ORIGIN:raise Rejected('exact_official_call_target_required')
  targets=(await self.backend.page.call('Target.getTargets')).get('targetInfos',[]);pages=[t for t in targets if t.get('type')=='page']
  if len(pages)!=1 or pages[0].get('targetId')!=root_target or pages[0].get('browserContextId','')!=info.get('browserContextId','') or urlsplit(pages[0].get('url','')).scheme+'://'+urlsplit(pages[0].get('url','')).netloc!=ORIGIN:raise Rejected('single_owned_WhatsApp_window_required')
  proof=dict(pid=main['pid'],start=main['start'],session=main['session'],group=main['group'],target=root_target,context=info.get('browserContextId',''),debug_sha256=hashlib.sha256(raw).hexdigest(),port=int(lines[0]),processes=[dict(pid=r['pid'],start=r['start']) for r in group])
  with self.db() as c:
   if c.execute('SELECT 1 FROM owners WHERE operation=?',(op,)).fetchone():raise Rejected('native_call_attestation_not_replayed')
   c.execute('INSERT INTO owners VALUES (?,?,?,?,?)',(op,owner,person,json.dumps(proof),'ATTESTED'));c.execute('INSERT INTO events(operation,state) VALUES (?,?)',(op,'ATTESTED'))
  return dict(operation_id=op,owned_browser=True,attestation_digest=digest(proof),automatic_retry=False)
 async def observe_gone(self,op,owner,person):
  r=self.row(op,owner,person);a=r['attestation'];rows=self.processes();same=[p for p in rows if p['pid']==a['pid'] and p['start']==a['start']]
  roots=self.profile_roots(rows)
  # A new private-profile browser/session must never be confused with the old
  # confirmed call, closed implicitly or accepted as proof that nothing lives.
  if roots or same or any(p['session']==a['session'] for p in rows):return dict(operation_id=op,state='HELD_UNCERTAIN',verified=False)
  original={p['pid']:p['start'] for p in a['processes']}
  if any(p['pid'] in original and p['start']==original[p['pid']] for p in rows):return dict(operation_id=op,state='HELD_UNCERTAIN',verified=False)
  # process_row deliberately omits unreadable/disappearing metadata; omission
  # alone is not process termination. PID reuse is conservatively ambiguous.
  if any(self.pid_exists(pid) for pid in original):return dict(operation_id=op,state='HELD_UNCERTAIN',verified=False)
  # Audio-node PID metadata alone cannot prove ownership, so reject any
  # remaining stream claiming a former owned PID, including ambiguous reuse.
  nodes=await self.graph()
  if not isinstance(nodes,list):raise Rejected('fresh_audio_graph_required')
  for n in nodes:
   if n.get('type')!='PipeWire:Interface:Node':continue
   p=n.get('info',{}).get('props',{});pid=str(p.get('application.process.id',''))
   if str(p.get('media.class','')).startswith('Stream/'):
    # An unattributed stream cannot be proven unrelated to this browser. No
    # global device change or process termination is used to resolve ambiguity.
    if not pid.isdigit() or int(pid) in original:return dict(operation_id=op,state='HELD_UNCERTAIN',verified=False)
  result=dict(operation_id=op,state='LOCAL_ENDED',verified=True,verification_source='fresh_owned_browser_session_and_audio_streams_gone',remote_end_acknowledged=False,attestation_digest=digest(a),automatic_retry=False)
  with self.db() as c:
   if c.execute("UPDATE owners SET state='LOCAL_ENDED' WHERE operation=? AND state!='LOCAL_ENDED'",(op,)).rowcount:
    c.execute('INSERT INTO events(operation,state) VALUES (?,?)',(op,'LOCAL_ENDED'))
  return result
 async def close_once(self,op,owner,person,*,authorized=False):
  if authorized is not True:raise Rejected('authenticated_GUI_End_call_required')
  r=self.row(op,owner,person)
  if r['state']!='ATTESTED':return await self.observe_gone(op,owner,person)
  a=r['attestation'];rows=self.processes();roots=self.profile_roots(rows)
  if len(roots)!=1 or roots[0]['pid']!=a['pid'] or roots[0]['start']!=a['start'] or roots[0]['session']!=a['session'] or roots[0]['group']!=a['group']:return await self.observe_gone(op,owner,person)
  debug=self.backend.profile()/'DevToolsActivePort'
  if debug.is_symlink() or hashlib.sha256(debug.read_bytes()).hexdigest()!=a['debug_sha256']:raise Rejected('owned_call_debug_binding_changed')
  self.backend.check();targets=(await self.backend.page.call('Target.getTargets')).get('targetInfos',[])
  pages=[t for t in targets if t.get('type')=='page']
  if (not 1<=len(pages)<=8 or not any(t.get('targetId')==a['target'] for t in pages)
      or any(urlsplit(t.get('url','')).scheme+'://'+urlsplit(t.get('url','')).netloc!=ORIGIN
             or t.get('browserContextId','')!=a.get('context','')
             or t.get('targetId')!=a['target'] and t.get('openerId')!=a['target'] for t in pages)):
   raise Rejected('foreign_or_missing_call_browser_target')
  with self.db() as c:
   if c.execute("UPDATE owners SET state='CLOSE_DISPATCHED' WHERE operation=? AND state='ATTESTED'",(op,)).rowcount!=1:raise Rejected('owned_call_close_not_replayed')
   c.execute('INSERT INTO events(operation,state) VALUES (?,?)',(op,'CLOSE_DISPATCHED'))
  # Browser.close is scoped by the exact private-profile debug endpoint above.
  # It never uses taskkill/pkill/signals or any primary/user browser profile.
  try:await self.backend.page.call('Browser.close')
  except asyncio.CancelledError:raise
  except Exception:pass
  end=time.monotonic()+6
  while True:
   result=await self.observe_gone(op,owner,person)
   if result['verified'] or time.monotonic()>end:return result
   await asyncio.sleep(.1)
