"""Private current-turn binding, monotonic queries and expiring contact handles."""
import json,time
from .guard import Rejected
from .recipients import Command,Directory,fingerprint,norm
from .contact_browser import ContactBrowser

class ContactService:
 def __init__(self,service):
  self.service=service;self.root=service.root;self.backend=service.backend
 def named_messages_enabled(self):
  p=self.root/'config/operating_policy.json'
  return p.is_file() and json.loads(p.read_bytes()).get('routine_whatsapp_contact_messages') is True
 def calls_enabled(self):
  p=self.root/'config/operating_policy.json'
  return p.is_file() and json.loads(p.read_bytes()).get('routine_whatsapp_calls') is True
 def directory(self):return Directory(self.root/'data/whatsapp-directory.sqlite3')
 def current(self,command):return self.service.active is True
 async def search(self,query,owner,goal,request,generation):
  command=Command(owner,goal,generation,request);normalized=command.permit_query(query)
  self.backend.check();d=self.directory();key=fingerprint([owner,goal,generation,normalized]);now=time.time()
  with d.db() as c:
   c.execute('CREATE TABLE IF NOT EXISTS attempts(sequence INTEGER PRIMARY KEY AUTOINCREMENT,key TEXT UNIQUE,owner TEXT,at REAL,state TEXT,result TEXT)')
   old=c.execute('SELECT * FROM attempts WHERE key=?',(key,)).fetchone()
   if old:
    if old['state']=='COMPLETED':
     result=json.loads(old['result'])
     for item in result.get('matches',[]):
      selected=c.execute('SELECT expires,revoked FROM selections WHERE id=?',(item['selection_id'],)).fetchone()
      if selected is None or selected['revoked'] or selected['expires']<=now:raise Rejected('contact_result_expired_no_automatic_requery')
     return result
    raise Rejected('contact_query_not_retried_after_failure')
   if c.execute('SELECT count(*) FROM attempts WHERE at>?',(now-60,)).fetchone()[0]>=8:raise Rejected('contact_query_rate_wait')
   c.execute('INSERT INTO attempts(key,owner,at,state) VALUES (?,?,?,?)',(key,owner,now,'STARTED'))
  try:
   async with self.backend.transaction(True):
    browser=ContactBrowser(self.backend);result=await d.search(browser,command,query,self.current)
    matches=result['matches']
    # One exact name may be inspected under this same lookup command. Partial
    # or multiple matches require a user selection, never first-match guessing.
    if len(matches)==1 and norm(matches[0]['label'])==normalized:
     resolved=d.resolve(command,matches[0]['selection_id'],self.current)
     verified=await browser.inspect_contact(resolved);self.backend.check()
     if verified.get('verified') is not True or verified.get('label')!=matches[0]['label']:raise Rejected('contact_identity_not_verified')
     with d.db() as c:
      destination=dict(label=verified['label'],phone=verified['phone'])
      c.execute('UPDATE selections SET destination=? WHERE id=?',(json.dumps(destination,ensure_ascii=False),matches[0]['selection_id']))
     d.remember_inspection(command,matches[0]['selection_id'],self.current,query)
     matches[0].update(phone=verified['phone'],identity_verified=True)
   self.backend.check();result.update(query=query,source='current_visible_WhatsApp_directory',messages_sent=0,calls_started=0,messaging_enabled=self.named_messages_enabled(),calling_enabled=self.calls_enabled())
   with d.db() as c:c.execute("UPDATE attempts SET state='COMPLETED',result=? WHERE key=?",(json.dumps(result,ensure_ascii=False),key))
   return result
  except BaseException:
   with d.db() as c:c.execute("UPDATE attempts SET state='UNVERIFIED' WHERE key=? AND state='STARTED'",(key,))
   raise

 async def inspect(self,selection_id,owner,goal,request,generation,reference_text=None):
  command=Command(owner,goal,generation,request);command.validate();d=self.directory()
  try:row=d.resolve_for_inspection(command,selection_id,self.current,reference_text)
  except Rejected:
   return dict(status='needs_clarification',selection_id=selection_id,reply='Please choose a specific person from the current contact results, or request a fresh lookup if that list has expired. No message or call has started.',messages_sent=0,calls_started=0,automatic_retry=False)
  async with self.backend.transaction(False):
   value=await ContactBrowser(self.backend).inspect_contact(row);self.backend.check()
  if value.get('verified') is not True:raise Rejected('contact_identity_not_verified')
  with d.db() as c:c.execute('UPDATE selections SET destination=? WHERE id=?',(json.dumps(dict(label=value['label'],phone=value['phone']),ensure_ascii=False),selection_id))
  d.remember_inspection(command,selection_id,self.current,reference_text)
  return dict(selection_id=selection_id,label=value['label'],phone=value['phone'],identity_verified=True,messages_sent=0,calls_started=0,messaging_enabled=self.named_messages_enabled(),calling_enabled=self.calls_enabled())
