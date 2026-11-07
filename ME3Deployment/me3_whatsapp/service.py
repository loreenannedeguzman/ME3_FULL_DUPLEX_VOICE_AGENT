"""Private prepared-message lifecycle, durable identities and scoped UI readback."""
import asyncio,hashlib,json,os,re,secrets,sqlite3,time,uuid
from contextlib import contextmanager,closing
from pathlib import Path
from .action import clean,fingerprint
from .guard import Guard,Approval,Rejected
class Store:
 def __init__(self,path):
  self.path=Path(path)
  if self.path.is_symlink():raise Rejected('untrusted_message_store')
  self.path.parent.mkdir(parents=True,exist_ok=True);fd=os.open(self.path,os.O_CREAT|os.O_RDWR|getattr(os,'O_NOFOLLOW',0),0o600);os.close(fd);os.chmod(self.path,0o600)
  with self.db() as c:
   c.execute('CREATE TABLE IF NOT EXISTS messages (operation_id TEXT PRIMARY KEY, token TEXT, intent TEXT, expected_digest TEXT, state TEXT, expires REAL, owner TEXT, binding TEXT, guard_id TEXT, observation TEXT)')
   c.execute('CREATE TABLE IF NOT EXISTS events (sequence INTEGER PRIMARY KEY AUTOINCREMENT, operation_id TEXT, state TEXT, at REAL)')
   c.execute('CREATE TABLE IF NOT EXISTS protected (text_sha256 TEXT PRIMARY KEY, reason TEXT)')
 @contextmanager
 def db(self):
  with closing(sqlite3.connect(self.path,timeout=3)) as c:
   c.row_factory=sqlite3.Row
   with c:yield c
 def row(self,op):
  with self.db() as c:r=c.execute('SELECT * FROM messages WHERE operation_id=?',(op,)).fetchone()
  if r is None:raise Rejected('unknown_owned_message_operation')
  result=dict(r)
  for k in ['intent','binding','observation']:result[k]=json.loads(result[k]) if result[k] else None
  return result
 def latest(self):
  with self.db() as c:r=c.execute('SELECT operation_id FROM messages ORDER BY rowid DESC LIMIT 1').fetchone()
  return self.row(r[0]) if r else None
 def protect(self,text_hash,reason):
  with self.db() as c:c.execute('INSERT OR IGNORE INTO protected VALUES (?,?)',(text_hash,reason))
 def blocked(self,text):
  with self.db() as c:return c.execute('SELECT 1 FROM protected WHERE text_sha256=?',(hashlib.sha256(text.encode()).hexdigest(),)).fetchone() is not None
 def transition(self,op,state,**fields):
  if set(fields)-{'binding','guard_id','observation'}:raise Rejected('invalid_message_state_update')
  with self.db() as c:
   updates=['state=?'];values=[state]
   for k,v in fields.items():updates.append(k+'=?');values.append(json.dumps(v) if k in {'binding','observation'} else v)
   values.append(op);c.execute('UPDATE messages SET '+','.join(updates)+' WHERE operation_id=?',values);c.execute('INSERT INTO events(operation_id,state,at) VALUES (?,?,?)',(op,state,time.time()))
 def prepare(self,op,recipient,text):
  intent=clean(recipient,text)
  if not re.fullmatch('[0-9a-f]{32}',op):raise Rejected('host_operation_identity_required')
  if self.blocked(text):raise Rejected('consumed_self_test_must_not_be_resent')
  with self.db() as c:
   old=c.execute('SELECT operation_id FROM messages WHERE operation_id=?',(op,)).fetchone()
   if not old:
    c.execute('INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?)',(op,secrets.token_hex(16),json.dumps(intent),fingerprint(intent),'PREPARED',time.time()+120,uuid.uuid4().hex,None,None,None));c.execute('INSERT INTO events(operation_id,state,at) VALUES (?,?,?)',(op,'PREPARED',time.time()))
  row=self.row(op)
  if row['intent']!=intent or row['state']!='PREPARED':raise Rejected('existing_message_intent_not_replayed')
  return dict(operation_id=op,intent=intent,expected_digest=row['expected_digest'],token=row['token'])
 def claim(self,op,token):
  row=self.row(op)
  if not secrets.compare_digest(row['token'],token):raise Rejected('private_host_token_required')
  if row['state']!='PREPARED':return False
  if time.time()>row['expires']:raise Rejected('message_review_expired')
  if self.blocked(row['intent']['text']):raise Rejected('consumed_self_test_must_not_be_resent')
  with self.db() as c:
   c.execute('BEGIN IMMEDIATE');n=c.execute("UPDATE messages SET state='DISPATCHED' WHERE operation_id=? AND state='PREPARED'",(op,)).rowcount
   if n:c.execute('INSERT INTO events(operation_id,state,at) VALUES (?,?,?)',(op,'DISPATCHED',time.time()))
  return n==1
 def import_existing(self,op,text,binding,observation):
  intent=clean('self',text);self.protect(hashlib.sha256(text.encode()).hexdigest(),'Owner explicitly prohibits resending the consumed historical self-test')
  with self.db() as c:
   if c.execute('SELECT 1 FROM messages WHERE operation_id=?',(op,)).fetchone():return self.row(op)
   c.execute('INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?)',(op,secrets.token_hex(16),json.dumps(intent),fingerprint(intent),'IMPORTED_OWNER_VERIFIED',0,uuid.uuid4().hex,json.dumps(binding),None,json.dumps(observation)));c.execute('INSERT INTO events(operation_id,state,at) VALUES (?,?,?)',(op,'IMPORTED_OWNER_VERIFIED',time.time()))
  return self.row(op)

class Service:
 def __init__(self,root,store,backend):
  self.root=Path(root);self.store=store;self.backend=backend;self.active=True;backend.current=lambda:self.active
  from .contact_service import ContactService
  self.contacts=ContactService(self)
  from .contact_outbound import ContactOutbound
  self.contact_outbound=ContactOutbound(self)
  from .window import WindowService,WindowStore
  self.window=WindowService(self,WindowStore(self.root/'data/whatsapp-window-operations.sqlite3'))
 async def prepare(self,op,recipient,text):return self.store.prepare(op,recipient,text)
 async def commit(self,op,token,confirmation,generation=1):
  if confirmation!='confirm':raise Rejected('direct_host_confirmation_required')
  if not self.store.claim(op,token):return dict(state=self.store.row(op)['state'],write_retried=False)
  row=self.store.row(op);intent=row['intent'];self.active=True
  try:
   async with self.backend.transaction():
    binding=await self.backend.compose(intent['text'],row['owner']);self.store.transition(op,'DISPATCHED',binding=binding)
    guard=Guard(self.root/'data/whatsapp-actions.sqlite3',self.backend,lambda owner,g:self.active and owner==row['owner'] and g==generation);approval=Approval(row['owner'],generation,intent['text'],True,authorization_id='whatsapp-'+op);prepared=await guard.prepare(approval);self.store.transition(op,'DISPATCHED',guard_id=prepared['operation_id']);result=await guard.commit(prepared['operation_id'],prepared['private_commit_token'],approval);observation=self.backend.last_observation or {}
    state='SUBMITTED' if result['state']=='SUBMITTED' else 'VERIFIED' if result['state']=='VERIFIED' else 'PENDING' if result['state']=='PENDING' else 'UNCERTAIN'
    self.store.transition(op,state,observation=observation);return dict(state=state,write_retried=False)
  except BaseException:self.store.transition(op,'UNCERTAIN');raise
  finally:self.active=False
 async def read_operation(self,op):
  row=self.store.row(op)
  if row['state']=='PREPARED':return dict(row=self.public_row(row),observation={},verification_source='prepared_only_no_effect')
  binding=row['binding'] or {};previous=row['observation'] or {}
  if not previous.get('message_id') and (binding.get('baseline_version')!=2 or binding.get('owner')!=row['owner'] or not isinstance(binding.get('before_ids'),list)):
   return dict(row=self.public_row(row),observation={},verification_source='no_trustworthy_message_binding_no_replay')
  self.active=True
  try:
   async with self.backend.transaction():
    await self.backend.open_self();observation=await self.backend.find(row['intent']['text'],message_id=previous.get('message_id'),before_ids=binding.get('before_ids',[]))
   if observation.get('exact_message') and observation.get('status') in {'submitted','sent','delivered','read'}:
    state='IMPORTED_OWNER_VERIFIED' if row['state']=='IMPORTED_OWNER_VERIFIED' else 'SUBMITTED' if observation['status']=='submitted' else 'VERIFIED';self.store.transition(op,state,observation=observation);row=self.store.row(op)
   public=dict(exact_outgoing_message=observation.get('exact_message') is True,recipient='self',text_sha256=hashlib.sha256(row['intent']['text'].encode()).hexdigest(),status=observation.get('status'),delivery_confirmed=observation.get('delivery_confirmed') is True,read_confirmed=observation.get('read_confirmed') is True)
   return dict(row=self.public_row(row),observation=public,verification_source='fresh_scoped_whatsapp_ui')
  finally:self.active=False
 def public_row(self,row):return {k:row[k] for k in ['operation_id','intent','expected_digest','state']}
 async def recent_status(self):
  row=self.store.latest()
  if not row:return dict(available=True,message=None,inbox_access=False,calling=json.loads((self.root/'config/operating_policy.json').read_bytes()).get('routine_whatsapp_calls') is True)
  data=await self.read_operation(row['operation_id']);o=data['observation'];return dict(available=True,message=dict(recipient='self',state=data['row']['state'],status=o.get('status'),visible_exact_outgoing=o.get('exact_outgoing_message') is True,delivery_confirmed=o.get('delivery_confirmed') is True,read_confirmed=o.get('read_confirmed') is True,historical_owner_phone_confirmation=row['state']=='IMPORTED_OWNER_VERIFIED'),verification_source=data['verification_source'],inbox_access=False,calling=json.loads((self.root/'config/operating_policy.json').read_bytes()).get('routine_whatsapp_calls') is True)
