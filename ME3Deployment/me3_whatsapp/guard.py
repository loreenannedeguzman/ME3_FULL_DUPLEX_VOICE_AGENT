"""Staged self-message dispatch kernel. Not registered with production MCP.

The host supplies approval/current generation. A model cannot mint approval.
Browser backend must independently bind the self-recipient and exact editor.
"""
import asyncio,hashlib,json,os,secrets,sqlite3,time
from contextlib import contextmanager,closing
from dataclasses import dataclass
from pathlib import Path

def digest(value):return hashlib.sha256(value.encode()).hexdigest()
class Rejected(RuntimeError):pass
@dataclass(frozen=True)
class Approval:
 owner:str
 generation:int
 text:str
 explicit_send:bool
 recipient:str='Message yourself'
 authorization_id:str=''
def bound_snapshot(value,approval):
 if not approval.explicit_send or approval.recipient!='Message yourself':raise Rejected('explicit_self_send_intent_required')
 if not isinstance(approval.authorization_id,str) or not 8<=len(approval.authorization_id)<=100:raise Rejected('host_authorization_identity_required')
 if not isinstance(value,dict) or value.get('official') is not True or value.get('linked') is not True or value.get('self_recipient') is not True:raise Rejected('verified_self_chat_required')
 if value.get('text')!=approval.text or not approval.text.strip() or len(approval.text)>4000:raise Rejected('exact_approved_editor_required')
 if not isinstance(value.get('recipient_key'),str) or not value['recipient_key']:raise Rejected('recipient_identity_required')
 if not isinstance(value.get('editor_revision'),str) or not value['editor_revision']:raise Rejected('editor_revision_required')
 return digest(json.dumps({k:value[k] for k in ['recipient_key','text','editor_revision']},sort_keys=True))
class Guard:
 def __init__(self,path,backend,current):
  self.path=Path(path);self.backend=backend;self.current=current;self.mutex=asyncio.Lock()
  if self.path.is_symlink():raise Rejected('untrusted_ledger')
  self.path.parent.mkdir(parents=True,exist_ok=True)
  fd=os.open(self.path,os.O_RDWR|os.O_CREAT|getattr(os,'O_NOFOLLOW',0),0o600);os.close(fd);os.chmod(self.path,0o600)
  with self.db() as c:
   c.execute('CREATE TABLE IF NOT EXISTS operations (id TEXT PRIMARY KEY, authorization_id TEXT UNIQUE, token_hash TEXT, owner TEXT, generation INTEGER, text_hash TEXT, snapshot_hash TEXT, recipient_key TEXT, state TEXT, observed_status TEXT)')
   c.execute('CREATE TABLE IF NOT EXISTS events (sequence INTEGER PRIMARY KEY AUTOINCREMENT, operation_id TEXT, state TEXT, observed_status TEXT, at REAL)')
 @contextmanager
 def db(self):
  with closing(sqlite3.connect(self.path,timeout=3)) as c:
   with c:yield c
 def event(self,c,operation_id,state,observed=None):c.execute('INSERT INTO events(operation_id,state,observed_status,at) VALUES (?,?,?,?)',(operation_id,state,observed,time.time()))
 def require_current(self,approval):
  if self.current(approval.owner,approval.generation) is not True:raise Rejected('obsolete_owner_or_generation')
 async def prepare(self,approval):
  async with self.mutex:
   self.require_current(approval);snapshot=await self.backend.snapshot();binding=bound_snapshot(snapshot,approval);self.require_current(approval)
   with self.db() as c:
    if c.execute('SELECT 1 FROM operations WHERE authorization_id=?',(approval.authorization_id,)).fetchone():raise Rejected('authorization_already_prepared_no_replay')
   operation_id=secrets.token_hex(16);token=secrets.token_hex(32)
   with self.db() as c:
    c.execute('INSERT INTO operations VALUES (?,?,?,?,?,?,?,?,?,?)',(operation_id,approval.authorization_id,digest(token),approval.owner,approval.generation,digest(approval.text),binding,snapshot['recipient_key'],'PREPARED',None));self.event(c,operation_id,'PREPARED')
   return dict(operation_id=operation_id,private_commit_token=token,state='PREPARED')
 def read(self,operation_id):
  with self.db() as c:
   c.row_factory=sqlite3.Row;row=c.execute('SELECT * FROM operations WHERE id=?',(operation_id,)).fetchone()
  if row is None:raise Rejected('unknown_operation')
  return dict(row)
 def state(self,operation_id,state,observed=None):
  with self.db() as c:
   c.execute('UPDATE operations SET state=?,observed_status=? WHERE id=?',(state,observed,operation_id));self.event(c,operation_id,state,observed)
 async def commit(self,operation_id,token,approval):
  async with self.mutex:
   row=self.read(operation_id)
   if not secrets.compare_digest(row['token_hash'],digest(token)):raise Rejected('private_commit_identity_required')
   if (row['owner'],row['generation'],row['text_hash'],row['authorization_id'])!=(approval.owner,approval.generation,digest(approval.text),approval.authorization_id):raise Rejected('approval_changed')
   # Never click twice, including after a process restart or ambiguous result.
   if row['state']!='PREPARED':return dict(operation_id=operation_id,state=row['state'],observed_status=row['observed_status'],replayed=False)
   self.require_current(approval);snapshot=await self.backend.snapshot()
   if bound_snapshot(snapshot,approval)!=row['snapshot_hash']:raise Rejected('recipient_or_editor_changed')
   self.require_current(approval)
   with self.db() as c:
    c.execute('BEGIN IMMEDIATE');updated=c.execute("UPDATE operations SET state='DISPATCHED' WHERE id=? AND state='PREPARED'",(operation_id,)).rowcount
    if updated==1:self.event(c,operation_id,'DISPATCHED')
   if updated!=1:raise Rejected('operation_already_claimed')
   try:
    # No await between last current check and invocation of host click method.
    self.require_current(approval);clicked=await self.backend.click_send()
    if clicked is not True:raise Rejected('send_click_unverified')
    observed=await self.backend.observe(approval.text,row['recipient_key'])
    if observed.get('exact_message') is not True or observed.get('recipient_key')!=row['recipient_key'] or observed.get('status') not in {'submitted','pending','queued','sent','delivered','read'}:
     self.state(operation_id,'UNCERTAIN');return dict(operation_id=operation_id,state='UNCERTAIN',observed_status=None,replayed=False)
    state='SUBMITTED' if observed['status']=='submitted' else 'PENDING' if observed['status'] in {'pending','queued'} else 'VERIFIED'
    self.state(operation_id,state,observed['status']);return dict(operation_id=operation_id,state=state,observed_status=observed['status'],replayed=False)
   except BaseException:
    # Cancellation/disconnection after durable dispatch cannot prove no effect.
    self.state(operation_id,'UNCERTAIN');raise
 async def cancel(self,operation_id):
  async with self.mutex:
   with self.db() as c:
    updated=c.execute("UPDATE operations SET state='CANCELLED' WHERE id=? AND state='PREPARED'",(operation_id,)).rowcount
    if updated==1:self.event(c,operation_id,'CANCELLED')
   row=self.read(operation_id)
   return dict(state=row['state'],draft_cleared=False,completed_actions_undone=False)
