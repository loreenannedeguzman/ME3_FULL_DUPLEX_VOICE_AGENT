"""Command-bound Gmail MCP; private exact-reviewed once-only SMTP lifecycle.

Mail content is untrusted reference data. Host provenance is not language
understanding: Onit interprets the direct request; this boundary owns authority.
"""
import argparse,asyncio,hashlib,json,logging,os,re,stat,threading,time,uuid
from pathlib import Path
from typing import Annotated
from mcp.server.fastmcp import FastMCP
from pydantic import Field,StrictInt,BeforeValidator
from speech_mcp.contracts import identity
from me3_assistant.reminder_server import exact_json_integer
from .credentials import load,CredentialError
from .transport import Transport,MailError,draft
from .store import Store,ReviewAuthority,CONFIRMATION

READ_TOOLS=[]
LEGACY_READ_TOOLS=['search_subject','list_recent_mail','read_message']
PRIVATE_TOOLS=['prepare_gmail_send','commit_gmail_send','read_gmail_operation','prepare_gmail_read','commit_gmail_read','read_gmail_read_operation']

def command_binding(owner,goal,generation,request):
 if any(not isinstance(v,str) or re.fullmatch('[0-9a-f]{32}',v) is None for v in (owner,goal)) or type(generation) is not int or generation<1:raise ValueError('current_gmail_owner_request_required')
 if not isinstance(request,str) or not request.strip() or len(request.encode())>4000 or any(ord(c)<32 and c not in '\n\t' for c in request):raise ValueError('bounded_gmail_owner_request_required')
 return dict(version=1,owner=owner,goal=goal,generation=generation,request_sha256=hashlib.sha256(request.encode()).hexdigest(),source='direct_current_user')

def load_authority(root,path=None):
 """Read only the dedicated owned mode600 key; never generate or expose it."""
 root=Path(root).absolute();expected=root/'data/gmail-host-key.json';path=Path(path or expected).absolute()
 if os.name!='posix' or root!=root.resolve() or path!=expected or any(p.is_symlink() for p in [path,*path.parents]):raise ValueError('private_pi_gmail_authority_path_required')
 if path.parent.stat().st_uid!=os.getuid():raise ValueError('owned_gmail_authority_directory_required')
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
 try:
  before=os.fstat(fd)
  if not stat.S_ISREG(before.st_mode) or before.st_uid!=os.getuid() or stat.S_IMODE(before.st_mode)!=0o600 or before.st_nlink!=1 or not 1<=before.st_size<=1024:raise ValueError('private_owned_gmail_authority_required')
  raw=os.read(fd,1025);after=os.fstat(fd);current=path.stat(follow_symlinks=False)
  signature=lambda s:(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_nlink,s.st_size,s.st_mtime_ns)
  if signature(before)!=signature(after) or signature(before)!=signature(current):raise ValueError('gmail_authority_changed')
  def pairs(items):
   result={}
   for k,v in items:
    if k in result:raise ValueError('duplicate_gmail_authority_field')
    result[k]=v
   return result
  value=json.loads(raw,object_pairs_hook=pairs)
  if set(value)!={'version','key'} or type(value['version']) is not int or value['version']!=1 or not isinstance(value['key'],str) or re.fullmatch('[0-9a-f]{64}',value['key']) is None:raise ValueError('exact_gmail_authority_required')
  return ReviewAuthority(bytes.fromhex(value['key']))
 finally:os.close(fd)

class Service:
 def __init__(self,store,transport,credentials,authority,*,clock=time.time):
  self.store=store;self.transport=transport;self.credentials=credentials;self.authority=authority;self.clock=clock
  self.bindings={};self.selections={};self.guard=threading.RLock();self.mutex=asyncio.Lock()
 def require_mail(self,*,send=False):
  if self.credentials is None or self.transport is None:raise CredentialError('gmail_credentials_unavailable')
  if send and self.authority is None:raise ValueError('gmail_confirmation_authority_unavailable')
 def bind(self,owner,goal,g,request):
  binding=command_binding(owner,goal,g,request)
  with self.guard:
   old=self.bindings.get(owner)
   if old and (old['generation']>g or old['generation']==g and old!=binding):raise ValueError('stale_gmail_owner_request')
   self.bindings[owner]=binding
  return binding
 def permit(self,binding,canceled,expected):
  def authorize(value):
   with self.guard:
    if canceled.is_set() or self.bindings.get(binding['owner'])!=binding or value!=expected:raise ValueError('gmail_command_permit_revoked')
    return True
  return authorize
 async def run(self,work,canceled):
  task=asyncio.create_task(asyncio.to_thread(work))
  try:return await asyncio.shield(task)
  except asyncio.CancelledError:
   with self.guard:canceled.set()
   # The already-running thread owns its durable outcome, even after speech
   # cancellation. Consume its exception; never start another send task.
   task.add_done_callback(lambda t:t.exception() if not t.cancelled() else None)
   raise
 def choices(self,owner,rows,version,mailbox='INBOX'):
  if mailbox not in ('INBOX','SENT'):raise ValueError('configured_mailbox_scope_required')
  with self.guard:
   self.selections={k:v for k,v in self.selections.items() if v['owner']!=owner and v['expires']>self.clock()}
   result=[]
   for position,row in enumerate(rows,1):
    handle=uuid.uuid4().hex;self.selections[handle]=dict(owner=owner,uid=row['uid'],uidvalidity=version,mailbox=mailbox,expires=self.clock()+120)
    result.append(dict(row,selection_id=handle,position=position,mailbox=mailbox))
   return result
 async def search(self,subject,limit,owner,goal,g,request):
  raise ValueError('gmail_read_requires_completed_scope_review_and_separate_confirmation')
 async def recent(self,limit,owner,goal,g,request):
  raise ValueError('gmail_read_requires_completed_scope_review_and_separate_confirmation')
 async def read(self,selection_id,max_bytes,owner,goal,g,request):
  raise ValueError('gmail_read_requires_completed_scope_review_and_separate_confirmation')
 async def _search(self,subject,limit,owner,goal,g,request,*,mailbox='INBOX'):
  if mailbox not in ('INBOX','SENT'):raise ValueError('configured_mailbox_scope_required')
  self.require_mail()
  b=self.bind(owner,goal,g,request);cancel=threading.Event();expected=dict(operation='search_subject',subject=subject,limit=limit,**({'mailbox':mailbox} if mailbox!='INBOX' else {}))
  data=await self.run(lambda:self.transport.search_subject(subject,limit=limit,authorize=self.permit(b,cancel,expected),**({'mailbox':mailbox} if mailbox!='INBOX' else {})),cancel)
  rows=self.choices(owner,[dict(uid=v) for v in reversed(data.pop('uids'))],data['uidvalidity'],mailbox)
  return dict(data,messages=rows,request_binding=b,subjects_not_read=True)
 async def _recent(self,limit,owner,goal,g,request,*,mailbox='INBOX'):
  if mailbox not in ('INBOX','SENT'):raise ValueError('configured_mailbox_scope_required')
  self.require_mail()
  b=self.bind(owner,goal,g,request);cancel=threading.Event();expected=dict(operation='list_recent_mail',mailbox=mailbox,limit=limit)
  data=await self.run(lambda:self.transport.list_recent_mail(limit=limit,authorize=self.permit(b,cancel,expected),**({'mailbox':mailbox} if mailbox!='INBOX' else {})),cancel)
  data['messages']=self.choices(owner,data['messages'],data['uidvalidity'],mailbox);return dict(data,request_binding=b)
 async def _read(self,selection_id,max_bytes,owner,goal,g,request,*,mailbox='INBOX'):
  if mailbox not in ('INBOX','SENT'):raise ValueError('configured_mailbox_scope_required')
  self.require_mail()
  b=self.bind(owner,goal,g,request)
  with self.guard:
   choice=self.selections.get(selection_id)
   if not choice or choice['owner']!=owner or choice.get('mailbox','INBOX')!=mailbox or not self.clock()<choice['expires']:raise ValueError('fresh_owner_mail_selection_required')
   choice=dict(choice)
  cancel=threading.Event();expected=dict(operation='read_message',mailbox=mailbox,uid=choice['uid'],uidvalidity=choice['uidvalidity'],max_bytes=max_bytes)
  data=await self.run(lambda:self.transport.read_message(choice['uid'],choice['uidvalidity'],max_bytes=max_bytes,authorize=self.permit(b,cancel,expected),**({'mailbox':mailbox} if mailbox!='INBOX' else {})),cancel)
  return dict(data,selection_id=selection_id,request_binding=b)
 def prepare(self,op,recipient,subject,body,owner,goal,g,request):
  self.require_mail(send=True)
  b=self.bind(owner,goal,g,request)
  recipient=self.credentials.account if recipient=='self' else recipient
  return dict(self.store.prepare(op,owner,draft(self.credentials,recipient,subject,body)),request_binding=b)
 async def commit(self,op,token,expected_digest,proof,owner,g):
  self.require_mail(send=True)
  # A signed exact review, not model prose, is required before even SMTP login.
  self.authority.verify(proof,op,owner,expected_digest,g)
  value=self.store.draft_for(op,owner);initial=self.store.read_operation(op,owner)
  if initial['state']!='PREPARED' or initial['expected_digest']!=expected_digest:raise ValueError('gmail_operation_not_prepared_no_replay')
  canceled=threading.Event()
  def authorize(current):
   with self.guard:
    if canceled.is_set() or current!=value:raise ValueError('gmail_send_permit_revoked')
    self.authority.verify(proof,op,owner,expected_digest,g);return True
  def claim(current):
   with self.guard:
    authorize(current);claimed=self.store.claim(op,owner,token,expected_digest,proof,generation=g)
    if claimed!=value:raise ValueError('gmail_claim_draft_changed')
    return True
  def work():
   try:
    receipt=self.transport.send_reviewed(value,authorize=authorize,claim_before_data=claim)
    # Preserve a late known SMTP250 even if the GUI request was canceled.
    self.store.outcome(op,owner,expected_digest,'SMTP_ACCEPTED',receipt)
    if not canceled.is_set():
     try:
      observed=self.transport.verify_sent(value,authorize=authorize)
      self.store.outcome(op,owner,expected_digest,'SENT_COPY_OBSERVED',observed)
     except MailError:pass # SMTP acceptance remains distinct, with no retry.
   except BaseException as exc:
    row=self.store.read_operation(op,owner)
    if row['state']=='PREPARED':self.store.cancel(op,owner,expected_digest)
    elif row['state']=='DISPATCHED':self.store.outcome(op,owner,expected_digest,'UNCERTAIN')
    if not isinstance(exc,MailError):raise
   return self.store.read_operation(op,owner)
  return await self.run(work,canceled)
 async def operation(self,op,owner,observe_sent=False):
  row=self.store.read_operation(op,owner)
  if observe_sent and self.credentials is not None and self.transport is not None and row['state'] in {'DISPATCHED','SMTP_ACCEPTED','UNCERTAIN'}:
   value=self.store.draft_for(op,owner);cancel=threading.Event()
   def authorize(current):
    if cancel.is_set() or current!=value or self.store.read_operation(op,owner)['claimed'] is not True:raise ValueError('durable_confirmed_gmail_claim_required')
    return True
   def work():
    try:
     observed=self.transport.verify_sent(value,authorize=authorize)
     self.store.outcome(op,owner,row['expected_digest'],'SENT_COPY_OBSERVED',observed)
    except MailError:pass
    return self.store.read_operation(op,owner)
   return await self.run(work,cancel)
  return row

def make_server(service):
 app=FastMCP('ME3 Guarded Gmail',log_level='CRITICAL')
 read_state=None
 def guarded_reads():
  # Pure schema discovery never derives keys, opens stores or accesses mail.
  nonlocal read_state
  if read_state is None:
   from .read_guard import GuardedReads,ReadAuthority
   authority=ReadAuthority.from_send_authority(service.authority) if service.authority is not None else None
   read_state=GuardedReads(service,authority,clock=service.clock)
  return read_state
 async def perform(g,r,fn,*,read=False,prepare=False):
  identity(g,r)
  async with service.mutex:
   try:
    value=fn();value=await value if hasattr(value,'__await__') else value
    return dict(value,ok=True,generation=g,request_id=r,server_role='utility')
   except asyncio.CancelledError:raise
   except (ValueError,MailError) as exc:
    if read or prepare and (service.credentials is None or service.authority is None):
     reason='gmail_credentials_unavailable' if isinstance(exc,CredentialError) else 'gmail_confirmation_authority_unavailable' if prepare else 'gmail_read_unavailable_or_selection_rejected'
     return dict(ok=True,available=False,status='unavailable',reason=reason,read_only=True,no_effect=True,content_is_untrusted=True,automatic_retry=False,generation=g,request_id=r,server_role='utility',guidance='No mailbox content or successful lookup is established. Explain this limitation; do not invent messages, retry automatically or initiate another account action.')
    return dict(ok=False,error='gmail_request_rejected_no_automatic_retry',generation=g,request_id=r,server_role='utility')
   except Exception:return dict(ok=False,error='gmail_unavailable_outcome_requires_status_no_retry',generation=g,request_id=r,server_role='utility')
 @app.tool()
 async def search_subject(subject:Annotated[str,Field(min_length=1,max_length=200)],generation:StrictInt,request_id:str,host_owner:str,host_goal:str,current_user_request:str,limit:Annotated[StrictInt,Field(ge=1,le=10),BeforeValidator(exact_json_integer)]=5)->dict:
  """DISABLED legacy read. Always refuses; use the private host-reviewed and separately confirmed read lifecycle. No mailbox access."""
  return await perform(generation,request_id,lambda:service.search(subject,limit,host_owner,host_goal,generation,current_user_request),read=True)
 @app.tool()
 async def list_recent_mail(generation:StrictInt,request_id:str,host_owner:str,host_goal:str,current_user_request:str,limit:Annotated[StrictInt,Field(ge=1,le=5),BeforeValidator(exact_json_integer)]=5)->dict:
  """DISABLED legacy read. Always refuses before authentication; no mailbox access."""
  return await perform(generation,request_id,lambda:service.recent(limit,host_owner,host_goal,generation,current_user_request),read=True)
 @app.tool()
 async def read_message(selection_id:Annotated[str,Field(pattern='^[0-9a-f]{32}$')],generation:StrictInt,request_id:str,host_owner:str,host_goal:str,current_user_request:str,max_bytes:Annotated[StrictInt,Field(ge=1024,le=65536),BeforeValidator(exact_json_integer)]=32768)->dict:
  """DISABLED legacy read. Always refuses before authentication; no message content access."""
  return await perform(generation,request_id,lambda:service.read(selection_id,max_bytes,host_owner,host_goal,generation,current_user_request),read=True)
 @app.tool()
 async def prepare_gmail_send(operation_id:str,recipient:str,subject:str,body:str,generation:StrictInt,request_id:str,host_owner:str,host_goal:str,current_user_request:str)->dict:
  """PRIVATE exact draft; no network/send. Never advertise as a model tool."""
  return await perform(generation,request_id,lambda:service.prepare(operation_id,recipient,subject,body,host_owner,host_goal,generation,current_user_request),prepare=True)
 @app.tool()
 async def commit_gmail_send(operation_id:str,token:str,expected_digest:str,proof:dict,host_owner:str,generation:StrictInt,request_id:str)->dict:
  """PRIVATE signed host review/direct confirmation, durable once-only DATA claim."""
  return await perform(generation,request_id,lambda:service.commit(operation_id,token,expected_digest,proof,host_owner,generation))
 @app.tool()
 async def read_gmail_operation(operation_id:str,host_owner:str,generation:StrictInt,request_id:str,observe_sent:bool=False)->dict:
  """PRIVATE fresh local evidence. Explicit host status check may observe exact Sent once; never sends/retries/polls."""
  return await perform(generation,request_id,lambda:service.operation(operation_id,host_owner,observe_sent))
 @app.tool()
 async def prepare_gmail_read(operation_id:str,kind:str,parameters:dict,host_owner:str,host_goal:str,generation:StrictInt,request_id:str,current_user_request:str)->dict:
  """PRIVATE pure read-scope proposal. No account access; host review and separate direct confirmation required. Never expose as a direct model tool."""
  return await perform(generation,request_id,lambda:guarded_reads().prepare(operation_id,host_owner,kind,parameters,host_goal,generation,current_user_request))
 @app.tool()
 async def commit_gmail_read(operation_id:str,token:str,expected_digest:str,proof:dict,host_owner:str,host_goal:str,generation:StrictInt,request_id:str,current_user_request:str)->dict:
  """PRIVATE exact scope signed host review + separate confirmation. One bounded read, no send or automatic retry."""
  return await perform(generation,request_id,lambda:guarded_reads().commit(operation_id,host_owner,token,expected_digest,proof,generation=generation,host_goal=host_goal,current_user_request=current_user_request))
 @app.tool()
 async def read_gmail_read_operation(operation_id:str,host_owner:str,generation:StrictInt,request_id:str)->dict:
  """PRIVATE cached read-operation state only. No account access, repeated read, sending or retry."""
  return await perform(generation,request_id,lambda:guarded_reads().check(operation_id,host_owner))
 return app

def main():
 from task_mcp.client import ROOT
 p=argparse.ArgumentParser();p.add_argument('--store',type=Path,default=ROOT/'data/gmail-operations.sqlite3');p.add_argument('--authority-key',type=Path,default=ROOT/'data/gmail-host-key.json');a=p.parse_args()
 try:authority=load_authority(ROOT,a.authority_key)
 except (ValueError,OSError):authority=None
 try:credentials=load(ROOT)
 except CredentialError:credentials=None
 store=Store(ROOT,a.store,authority=authority);store.initialize();logging.disable(logging.CRITICAL)
 make_server(Service(store,Transport(credentials) if credentials is not None else None,credentials,authority)).run(transport='stdio')
if __name__=='__main__':main()
