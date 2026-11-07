"""Once-only call lifecycle kernel reused by the private host calling service.

The eventual adapter must use ME3's completed exact recipient/call-type review
and separate direct confirmation. This kernel never interprets user wording,
searches contacts, selects a person, opens an app, or grants permissions.
Browser/audio implementations must be independently qualified before binding.
"""
import asyncio,hashlib,json,os,re,secrets,sqlite3,time
from contextlib import contextmanager,closing
from pathlib import Path

class Refused(RuntimeError):pass
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
def identity(value):
 if not isinstance(value,str) or not re.fullmatch('[0-9a-f]{32}',value):raise Refused('host_identity_required')
 return value

class CallLifecycle:
 def __init__(self,root,path,browser,audio,resolve,current,allowed,clock=time.time):
  self.root=Path(root).resolve();self.path=Path(path);self.browser=browser;self.audio=audio;self.resolve=resolve;self.current=current;self.allowed=allowed;self.clock=clock;self.mutex=asyncio.Lock()
  if self.path.is_symlink() or not self.path.resolve().is_relative_to(self.root):raise Refused('private_call_store_required')
  self.path.parent.mkdir(parents=True,exist_ok=True)
  fd=os.open(self.path,os.O_CREAT|os.O_RDWR|getattr(os,'O_NOFOLLOW',0),0o600);os.close(fd)
  with self.db() as c:
   c.execute('CREATE TABLE IF NOT EXISTS calls (id TEXT PRIMARY KEY, owner TEXT, token TEXT, intent TEXT, intent_hash TEXT, binding TEXT, expires REAL, state TEXT, session TEXT)')
   c.execute('CREATE TABLE IF NOT EXISTS hangups (id TEXT PRIMARY KEY, state TEXT)')
   c.execute('CREATE TABLE IF NOT EXISTS events (sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT, state TEXT, at REAL)')
   c.execute('CREATE TABLE IF NOT EXISTS call_slot (id INTEGER PRIMARY KEY CHECK(id=1), operation TEXT UNIQUE)')
   c.execute('CREATE TABLE IF NOT EXISTS observations (operation TEXT PRIMARY KEY, receipt TEXT, sequence INTEGER, rank INTEGER)')
 @contextmanager
 def db(self):
  with closing(sqlite3.connect(self.path,timeout=3)) as c:
   c.row_factory=sqlite3.Row
   try:
    c.execute('PRAGMA synchronous=FULL');c.execute('BEGIN IMMEDIATE');yield c;c.commit()
   except BaseException:c.rollback();raise
 def row(self,op):
  identity(op)
  with self.db() as c:r=c.execute('SELECT * FROM calls WHERE id=?',(op,)).fetchone()
  if r is None:raise Refused('unknown_call_operation')
  v=dict(r)
  for k in ['intent','binding']:v[k]=json.loads(v[k])
  return v
 def transition(self,op,state,session=None):
  if state!='UNCERTAIN':raise Refused('call_progress_requires_independent_observation')
  with self.db() as c:
   r=c.execute('SELECT state,session FROM calls WHERE id=?',(op,)).fetchone()
   if r is None:raise Refused('unknown_call_operation')
   if session is not None and r['session'] not in (None,session):raise Refused('call_session_changed')
   if r['state'] in {'ENDED','LOCAL_ENDED','CANCELED','ABORTED'} and state!=r['state']:
    if state=='UNCERTAIN':
     c.execute('INSERT INTO events(id,state,at) VALUES (?,?,?)',(op,'POST_TERMINAL_FAILURE',self.clock()));return
    raise Refused('terminal_call_state_cannot_regress')
   c.execute('UPDATE calls SET state=?,session=COALESCE(?,session) WHERE id=?',(state,session,op));c.execute('INSERT INTO events(id,state,at) VALUES (?,?,?)',(op,state,self.clock()))
 def claimed(self,owner,op):
  with self.db() as c:
   r=c.execute('SELECT owner FROM calls WHERE id=?',(op,)).fetchone()
   slot=c.execute('SELECT operation FROM call_slot WHERE id=1').fetchone()
   return bool(r and r['owner']==owner and slot and slot['operation']==op)
 def no_dial_aborted(self,owner,op):
  with self.db() as c:
   r=c.execute('SELECT owner,state,session FROM calls WHERE id=?',(op,)).fetchone()
   sent=c.execute("SELECT 1 FROM events WHERE id=? AND state='DISPATCHED'",(op,)).fetchone()
   return bool(r and r['owner']==owner and r['state']=='ABORTED' and r['session'] is None and sent is None)
 def observation(self,op):
  with self.db() as c:r=c.execute('SELECT receipt FROM observations WHERE operation=?',(op,)).fetchone()
  if r is None:raise Refused('independent_observation_missing')
  return json.loads(r['receipt'])
 def free_slot(self,op):
  with self.db() as c:c.execute('DELETE FROM call_slot WHERE id=1 AND operation=?',(op,))
 def check(self,owner,generation):
  if type(generation) is not int or generation<1 or not self.current(owner,generation):raise asyncio.CancelledError()
 def selected(self,owner,selection):
  person=self.resolve(owner,identity(selection))
  if not isinstance(person,dict) or person.get('selection_id')!=selection or person.get('owner')!=owner or person.get('identity_verified') is not True or type(person.get('expires')) not in (int,float) or person['expires']<=self.clock():raise Refused('current_visibly_verified_selection_required')
  if not re.fullmatch(r'\+[1-9][0-9]{6,14}',person.get('phone','')) or not person.get('label') or not person.get('target_id') or not person.get('binding'):raise Refused('exact_contact_binding_required')
  if not self.allowed(person):raise Refused('recipient_outside_call_qualification_scope')
  return json.loads(json.dumps(person))
 async def prepare(self,op,owner,generation,selection,kind,request_sha256):
  identity(op);identity(owner);self.check(owner,generation)
  if kind not in {'voice','video'} or not re.fullmatch('[0-9a-f]{64}',request_sha256 or ''):raise Refused('exact_call_kind_and_request_binding_required')
  person=self.selected(owner,selection);intent=dict(recipient=dict(selection_id=selection,label=person['label'],phone=person['phone']),kind=kind)
  token=secrets.token_hex(32);binding=dict(person=person,request_sha256=request_sha256,generation=generation)
  # Intent storage only. No browser snapshot/navigation or audio handoff here.
  with self.db() as c:
   if c.execute('SELECT 1 FROM call_slot').fetchone():raise Refused('prior_call_requires_reconciliation_or_hangup')
   if c.execute("SELECT 1 FROM calls WHERE state IN ('DISPATCHED','UNCERTAIN','DIALING','RINGING','CONNECTED')").fetchone():raise Refused('prior_call_requires_reconciliation_or_hangup')
   if c.execute('SELECT 1 FROM calls WHERE id=?',(op,)).fetchone():raise Refused('call_operation_not_replayed')
   c.execute('INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?)',(op,owner,token,json.dumps(intent),digest(intent),json.dumps(binding),min(self.clock()+120,person['expires']),'PREPARED',None))
   c.execute('INSERT INTO events(id,state,at) VALUES (?,?,?)',(op,'PREPARED',self.clock()))
  return dict(operation_id=op,private_token=token,intent=intent,expected_digest=digest(intent))
 def cancel(self,op):
  with self.db() as c:
   changed=c.execute("UPDATE calls SET state='CANCELED' WHERE id=? AND state='PREPARED'",(op,)).rowcount
   if changed:c.execute('INSERT INTO events(id,state,at) VALUES (?,?,?)',(op,'CANCELED',self.clock()))
  return self.public(op)
 def public(self,op):
  r=self.row(op);return dict(operation_id=op,intent=r['intent'],state=r['state'],session_id=r['session'],audio_held=self.claimed(r['owner'],op),redialed=False)
 async def commit(self,op,token,confirmation,generation):
  async with self.mutex:
   row=self.row(op)
   if not isinstance(token,str) or not secrets.compare_digest(row['token'],token) or confirmation!='confirm':raise Refused('private_host_confirm_required')
   if row['state']!='PREPARED':return self.public(op)
   self.check(row['owner'],generation)
   if row['expires']<=self.clock() or digest(row['intent'])!=row['intent_hash']:raise Refused('expired_or_changed_call_review')
   person=self.selected(row['owner'],row['intent']['recipient']['selection_id'])
   if person!=row['binding']['person']:raise Refused('reviewed_contact_changed')
   # A durable global claim precedes even read-only browser/audio touches. A
   # per-instance asyncio mutex alone cannot exclude a competing process.
   with self.db() as c:
    if c.execute('SELECT 1 FROM call_slot').fetchone() or c.execute("SELECT 1 FROM calls WHERE id!=? AND state IN ('CLAIMED','DISPATCHED','UNCERTAIN','DIALING','RINGING','CONNECTED')",(op,)).fetchone():raise Refused('another_call_requires_reconciliation_or_hangup')
    if c.execute("UPDATE calls SET state='CLAIMED' WHERE id=? AND state='PREPARED'",(op,)).rowcount!=1:raise Refused('call_already_claimed')
    c.execute('INSERT INTO call_slot VALUES (1,?)',(op,));c.execute('INSERT INTO events(id,state,at) VALUES (?,?,?)',(op,'CLAIMED',self.clock()))
   audio_attempted=False
   try:
    before=await self.browser.snapshot(person,row['intent']['kind']);self.check(row['owner'],generation)
    if before.get('bound') is not True or before.get('target_id')!=person['target_id'] or before.get('active_call') is not False or before.get('call_available') is not True or before.get('unknown_dialog') is not False:raise Refused('supported_bound_inactive_call_control_required')
    audio_attempted=True;lease=await self.audio.acquire(row['owner'],op)
    if not isinstance(lease,dict) or lease.get('ready') is not True or lease.get('asr_suspended') is not True or lease.get('assistant_output_quiet') is not True or lease.get('owned_lease') is not True:raise Refused('verified_private_call_audio_handoff_required')
    self.check(row['owner'],generation)
    with self.db() as c:
     slot=c.execute('SELECT operation FROM call_slot WHERE id=1').fetchone()
     if not slot or slot['operation']!=op:raise Refused('call_claim_changed')
     changed=c.execute("UPDATE calls SET state='DISPATCHED' WHERE id=? AND state='CLAIMED'",(op,)).rowcount
     if changed!=1:raise Refused('call_already_claimed')
     c.execute('INSERT INTO events(id,state,at) VALUES (?,?,?)',(op,'DISPATCHED',self.clock()))
    await self.audio.mark_dispatch(row['owner'],op)
    self.check(row['owner'],generation)
    session=await self.browser.dial_once(person,row['intent']['kind'],before)
    if not isinstance(session,str) or not re.fullmatch('[A-Za-z0-9_-]{8,128}',session):raise Refused('owned_call_session_required')
    self.transition(op,'UNCERTAIN',session)
    observation=await self.browser.observe(session,person);self.record_observation(op,observation)
    await self.audio.sync_observation(row['owner'],op)
    if self.row(op)['state']=='ENDED':await self.release_ended(op)
    return self.public(op)
   except BaseException:
    with self.db() as c:
     aborted=c.execute("UPDATE calls SET state='ABORTED' WHERE id=? AND state='CLAIMED'",(op,)).rowcount
     if aborted:c.execute('INSERT INTO events(id,state,at) VALUES (?,?,?)',(op,'ABORTED',self.clock()))
    if aborted:
     if audio_attempted:await self.audio.abort_before_dispatch(row['owner'],op)
     self.free_slot(op)
    else:
     self.transition(op,'UNCERTAIN')
     await self.audio.uncertain(row['owner'],op)
    raise
 def record_observation(self,op,value):
  row=self.row(op)
  if not isinstance(value,dict) or value.get('session_id')!=row['session'] or value.get('recipient_phone')!=row['intent']['recipient']['phone'] or value.get('fresh') is not True or value.get('state') not in {'dialing','ringing','connected','ended'} or type(value.get('sequence')) is not int or value['sequence']<1 or self.browser.verify_observation(value,row['binding']['person']) is not True:raise Refused('fresh_exact_call_outcome_required')
  rank={'dialing':1,'ringing':2,'connected':3,'ended':4}[value['state']]
  with self.db() as c:
   actual=c.execute('SELECT state,session FROM calls WHERE id=?',(op,)).fetchone()
   prior=c.execute('SELECT sequence,rank FROM observations WHERE operation=?',(op,)).fetchone()
   if actual['session']!=value['session_id'] or actual['state'] in {'PREPARED','CLAIMED','CANCELED','ABORTED','LOCAL_ENDED'}:raise Refused('dispatched_exact_call_required')
   if actual['state']=='ENDED' and rank!=4 or prior and (value['sequence']<=prior['sequence'] or rank<prior['rank']):raise Refused('call_observation_replay_or_regression')
   c.execute('INSERT INTO observations VALUES (?,?,?,?) ON CONFLICT(operation) DO UPDATE SET receipt=excluded.receipt,sequence=excluded.sequence,rank=excluded.rank',(op,json.dumps(value),value['sequence'],rank))
   c.execute('UPDATE calls SET state=? WHERE id=?',(value['state'].upper(),op));c.execute('INSERT INTO events(id,state,at) VALUES (?,?,?)',(op,value['state'].upper(),self.clock()))
 async def release_ended(self,op):
  r=self.row(op)
  if r['state']!='ENDED':raise Refused('independently_ended_call_required')
  await self.audio.release(r['owner'],op,self.observation(op));self.free_slot(op)
 async def reconcile(self,op):
  async with self.mutex:
   row=self.row(op)
   if row['state']=='ENDED':
    if self.claimed(row['owner'],op):await self.release_ended(op)
    return self.public(op)
   if row['state'] not in {'UNCERTAIN','DIALING','RINGING','CONNECTED'} or not row['session']:return self.public(op)
   self.record_observation(op,await self.browser.observe(row['session'],row['binding']['person']))
   await self.audio.sync_observation(row['owner'],op)
   if self.row(op)['state']=='ENDED':await self.release_ended(op)
   return self.public(op)

 async def hangup(self,op,owner,generation=None,*,request=None):
  """Authenticated GUI-only host API; independent of suspended voice epoch."""
  async with self.mutex:
   row=self.row(op);identity(owner)
   if row['owner']!=owner:raise Refused('call_owner_changed')
   if row['state']=='ENDED':return self.public(op)
   if row['state'] not in {'UNCERTAIN','DIALING','RINGING','CONNECTED'} or not row['session']:raise Refused('owned_active_call_session_required')
   await self.audio.authorize_hangup(owner,op,request)
   # A previous uncertain hangup must be read back, never clicked again.
   with self.db() as c:
    if c.execute('SELECT 1 FROM hangups WHERE id=?',(op,)).fetchone():raise Refused('hangup_not_replayed_reconcile_only')
    c.execute('INSERT INTO hangups VALUES (?,?)',(op,'DISPATCHED'))
   try:
    if await self.browser.hangup_once(row['session'],row['binding']['person']) is not True:raise Refused('hangup_click_not_verified')
    self.record_observation(op,await self.browser.observe(row['session'],row['binding']['person']))
    await self.audio.sync_observation(owner,op)
    if self.row(op)['state']!='ENDED':raise Refused('call_end_not_verified')
    await self.release_ended(op)
    with self.db() as c:c.execute("UPDATE hangups SET state='VERIFIED' WHERE id=?",(op,))
    return self.public(op)
   except BaseException:
    self.transition(op,'UNCERTAIN')
    with self.db() as c:c.execute("UPDATE hangups SET state='UNCERTAIN' WHERE id=?",(op,))
    raise
