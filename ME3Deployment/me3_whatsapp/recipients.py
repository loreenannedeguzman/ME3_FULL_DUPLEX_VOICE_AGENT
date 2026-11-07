"""General commanded recipient selection. Host-only browser/MCP binding; no message or call authority.

No address book export, background lookup, guessed recipient or name-as-identity.
The host constructs Command from the direct current user turn, never tool text.
"""
import hashlib,json,re,secrets,sqlite3,time,unicodedata
from contextlib import contextmanager,closing
from dataclasses import dataclass
from pathlib import Path
from me3_whatsapp.guard import Rejected

LOOKUP_SECONDS=120
VERIFIED_CONTACT_SECONDS=1800 # Absolute from visible verification, never refreshed by talk/drafts/confirmation.

def norm(value):
 return ' '.join(unicodedata.normalize('NFKC',value).casefold().split())
def command_text(value):
 """Normalize courteous/direct-request wrappers, never infer an action or person."""
 text=norm(value).replace('’',"'")
 prefix=r"^(?:(?:please)[, ]+|(?:can|could|would|will) you(?: please)?[, ]+|(?:i (?:would like|want|need)|i'd like) (?:you )?to )"
 for _ in range(8):
  updated=re.sub(prefix,'',text)
  if updated==text:break
  text=updated
 return text

def fingerprint(value):
 return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
def exact_text(value,limit):
 if not isinstance(value,str) or value!=value.strip() or not value or len(value.encode())>limit or any(ord(c)<32 for c in value):raise Rejected('bounded_exact_text_required')
 return value
@dataclass(frozen=True)
class Command:
 owner:str
 goal:str
 generation:int
 request:str
 source:str='direct_current_user'
 def validate(self):
  if self.source!='direct_current_user' or any(not isinstance(x,str) or not re.fullmatch('[0-9a-f]{32}',x) for x in [self.owner,self.goal]) or type(self.generation) is not int or self.generation<1:raise Rejected('current_host_command_required')
  exact_text(self.request,4000)
 def permit_query(self,query):
  self.validate();exact_text(query,160);q=norm(query)
  # Onit interprets whether the direct current owner commands this lookup.
  # Deterministic controls bind source, literal query, privacy bounds and
  # obvious quoted/reported/negated requests, not an utterance verb catalogue.
  if q in {'all','everyone','*','contacts','my contacts','directory','address book'} or len(q)<2:raise Rejected('specific_recipient_query_required')
  plain=re.sub(r'"[^"\n]*"|“[^”\n]*”|(?<!\w)\'[^\'\n]*\'(?!\w)',' ',self.request);r=norm(plain)
  if re.search(r"\b(?:do not|don\W?t|never|not now|without|instead of|if i|when i|example|for example|quote|said|says|told)\b",r):raise Rejected('unambiguous_current_lookup_command_required')
  if not re.search(r'(?<!\w)'+re.escape(q)+r'(?!\w)',r):raise Rejected('query_not_in_current_owner_text')
  return q

class Directory:
 """Private expiring result handles; no query or recipient plaintext in audit."""
 def __init__(self,path,clock=time.time):
  self.path=Path(path);self.clock=clock
  if self.path.is_symlink():raise Rejected('untrusted_recipient_store')
  self.path.parent.mkdir(parents=True,exist_ok=True)
  import os
  fd=os.open(self.path,os.O_CREAT|os.O_RDWR|getattr(os,'O_NOFOLLOW',0),0o600);os.close(fd)
  if os.name!='nt':os.chmod(self.path,0o600)
  with self.db() as c:
   c.execute('CREATE TABLE IF NOT EXISTS selections (id TEXT PRIMARY KEY, owner TEXT, goal TEXT, query_hash TEXT, destination TEXT, binding TEXT, target TEXT, expires REAL, revoked INTEGER NOT NULL DEFAULT 0)')
   c.execute('CREATE TABLE IF NOT EXISTS selected_contacts (owner TEXT PRIMARY KEY, selection_id TEXT NOT NULL, expires REAL NOT NULL)')
   c.execute('CREATE TABLE IF NOT EXISTS queries (sequence INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT, goal TEXT, query_hash TEXT, at REAL)')
 @contextmanager
 def db(self):
  with closing(sqlite3.connect(self.path,timeout=3)) as c:
   c.row_factory=sqlite3.Row
   with c:yield c
 async def search(self,backend,command,query,current):
  normalized=command.permit_query(query)
  if not current(command):raise Rejected('obsolete_lookup_command')
  # Charge/record before the one UI query. Unknown outcomes are not retried.
  with self.db() as c:
   c.execute('UPDATE selections SET revoked=1 WHERE owner=?',(command.owner,))
   c.execute('DELETE FROM selected_contacts WHERE owner=?',(command.owner,))
   c.execute('INSERT INTO queries(owner,goal,query_hash,at) VALUES (?,?,?,?)',(command.owner,command.goal,fingerprint(normalized),self.clock()))
  result=await backend.search_contacts(query)
  if not current(command):raise Rejected('obsolete_lookup_result')
  if not isinstance(result,dict) or result.get('official') is not True or result.get('linked') is not True or not isinstance(result.get('target_id'),str) or not result['target_id'] or result.get('query')!=query:raise Rejected('scoped_directory_result_required')
  rows=result.get('matches')
  if not isinstance(rows,list) or len(rows)>8 or result.get('truncated') is True:raise Rejected('narrow_query_before_contact_selection')
  accepted=[];bindings=set()
  for row in rows:
   if not isinstance(row,dict) or row.get('kind')!='person':raise Rejected('verified_person_results_only')
   label=exact_text(row.get('label'),200);binding=exact_text(row.get('binding'),256)
   phone=row.get('phone')
   if phone is not None and (not isinstance(phone,str) or not re.fullmatch(r'\+[1-9][0-9]{6,14}',phone)):raise Rejected('verified_E164_number_required')
   if binding in bindings:raise Rejected('ambiguous_contact_binding')
   bindings.add(binding);accepted.append(dict(label=label,phone=phone,binding=binding))
  # Duplicate names remain separate inspectable rows with explicit positions.
  # Outbound resolve() still requires the distinguishing verified phone.
  output=[]
  with self.db() as c:
   for row in accepted:
    handle=secrets.token_hex(16);destination={k:row[k] for k in ['label','phone']}
    c.execute('INSERT INTO selections VALUES (?,?,?,?,?,?,?,?,0)',(handle,command.owner,command.goal,fingerprint(normalized),json.dumps(destination,ensure_ascii=False),row['binding'],result['target_id'],self.clock()+LOOKUP_SECONDS))
    output.append(dict(selection_id=handle,position=len(output)+1,identity_verified=row['phone'] is not None,**destination))
  return dict(matches=output,selection_required=True,no_action_started=True)
 def resolve_for_inspection(self,command,selection_id,current,reference_text=None):
  command.validate()
  if not current(command):raise Rejected('obsolete_lookup_command')
  with self.db() as c:
   rows=c.execute('SELECT * FROM selections WHERE owner=? AND revoked=0 AND expires>? ORDER BY rowid',(command.owner,self.clock())).fetchall()
  offered=[dict(selection_id=r['id'],position=i,label=json.loads(r['destination'])['label']) for i,r in enumerate(rows,1)]
  inspection_choice(command.request,offered,selection_id,reference_text)
  row=next(r for r in rows if r['id']==selection_id)
  return dict(selection_id=row['id'],destination=json.loads(row['destination']),binding=row['binding'],target_id=row['target'],owner=command.owner,goal=command.goal,lookup_goal=row['goal'],reference_eligible=len(rows)==1)
 def remember_inspection(self,command,selection_id,current,reference_text=None):
  # Only the completed visible-phone inspection path records this reference.
  row=self.resolve_for_inspection(command,selection_id,current,reference_text)
  if not isinstance(row['destination'].get('phone'),str) or not re.fullmatch(r'\+[1-9][0-9]{6,14}',row['destination']['phone']):raise Rejected('verified_contact_required_for_reference')
  with self.db() as c:
   found=c.execute('SELECT expires FROM selections WHERE id=? AND owner=? AND revoked=0',(selection_id,command.owner)).fetchone()
   if found is None or found['expires']<=self.clock():raise Rejected('fresh_owned_recipient_selection_required')
   previous=c.execute('SELECT selection_id,expires FROM selected_contacts WHERE owner=?',(command.owner,)).fetchone()
   # A chosen, visibly verified person outlives the short unselected result list.
   # Same-person observation never slides the original verification deadline.
   expiry=min(previous['expires'],self.clock()+VERIFIED_CONTACT_SECONDS) if previous and previous['selection_id']==selection_id else self.clock()+VERIFIED_CONTACT_SECONDS
   c.execute('UPDATE selections SET expires=? WHERE id=? AND owner=? AND revoked=0',(expiry,selection_id,command.owner))
   c.execute('INSERT OR REPLACE INTO selected_contacts VALUES (?,?,?)',(command.owner,selection_id,expiry))
 def resolve(self,command,selection_id,current):
  command.validate()
  if not re.fullmatch('[0-9a-f]{32}',selection_id or ''):raise Rejected('host_selection_handle_required')
  with self.db() as c:row=c.execute('SELECT * FROM selections WHERE id=?',(selection_id,)).fetchone()
  if row is None or row['revoked'] or row['expires']<=self.clock() or row['owner']!=command.owner or not current(command):raise Rejected('fresh_owned_recipient_selection_required')
  with self.db() as c:active=c.execute('SELECT destination FROM selections WHERE owner=? AND revoked=0 AND expires>?',(command.owner,self.clock())).fetchall()
  destination=json.loads(row['destination']);r=command_text(command.request);label=norm(destination['label']);phone=destination.get('phone')
  # New conversational goals may refer to the one just-found person. A
  # directory lookup must not be tied to one planner turn or fixed test case.
  with self.db() as c:chosen=c.execute('SELECT selection_id,expires FROM selected_contacts WHERE owner=?',(command.owner,)).fetchone()
  explicitly_selected=bool(chosen and chosen['selection_id']==selection_id and chosen['expires']>self.clock() and phone)
  unique=len(active)==1 or explicitly_selected
  exact_name=bool(re.search(r'(?<!\w)'+re.escape(label)+r'(?!\w)',r))
  exact_phone=bool(phone and phone in command.request)
  reference=unique and bool(re.search(r'\b(?:her|him|them|this contact|that contact|this person|that person)\b',r))
  duplicates=sum(norm(json.loads(a['destination'])['label'])==label for a in active)>1
  if not (exact_name or exact_phone or reference) or duplicates and not (exact_phone or explicitly_selected):raise Rejected('clarify_the_specific_person_before_selection')
  if command.goal!=row['goal'] and not re.match(r'^(?:(?:please|can you|could you|would you)\s+)*(?:send|message|text|call|phone|ring)\b',r):raise Rejected('fresh_followup_command_required')
  return dict(selection_id=selection_id,destination=destination,binding=row['binding'],target_id=row['target'],owner=command.owner,goal=command.goal,lookup_goal=row['goal'],reference_eligible=unique)
 def resolve_draft(self,command,selection_id,current):
  """Resolve only an already selected/uniquely verified person; no language parsing.

  This prepares data for exact review. It never searches, navigates, composes
  or sends. Confirmation still revalidates this original expiring binding.
  """
  command.validate()
  if not re.fullmatch('[0-9a-f]{32}',selection_id or ''):raise Rejected('host_selection_handle_required')
  if not current(command):raise Rejected('obsolete_draft_request')
  with self.db() as c:
   row=c.execute('SELECT * FROM selections WHERE id=? AND owner=? AND revoked=0 AND expires>?',(selection_id,command.owner,self.clock())).fetchone()
   chosen=c.execute('SELECT selection_id,expires FROM selected_contacts WHERE owner=?',(command.owner,)).fetchone()
   count=c.execute('SELECT count(*) FROM selections WHERE owner=? AND revoked=0 AND expires>?',(command.owner,self.clock())).fetchone()[0]
  if row is None:raise Rejected('fresh_owned_recipient_selection_required')
  destination=json.loads(row['destination']);phone=destination.get('phone')
  if not isinstance(phone,str) or not re.fullmatch(r'\+[1-9][0-9]{6,14}',phone):raise Rejected('visibly_verified_recipient_phone_required')
  selected=bool(chosen and chosen['selection_id']==selection_id and chosen['expires']>self.clock())
  if not selected and not (count==1 and chosen is None):raise Rejected('selected_verified_recipient_required')
  return dict(selection_id=selection_id,destination=destination,binding=row['binding'],target_id=row['target'],owner=command.owner,goal=command.goal,lookup_goal=row['goal'],reference_eligible=True)
 def revoke(self,owner):
  with self.db() as c:c.execute('UPDATE selections SET revoked=1 WHERE owner=?',(owner,))

def message_intent(recipient,text):
 if not isinstance(text,str) or not text or text!=text.strip() or len(text.encode())>2000 or any(ord(c)<32 and c not in '\n\t' for c in text):raise Rejected('exact_message_text_required')
 return dict(recipient=recipient,text=text)
def call_intent(recipient,kind):
 if kind not in {'voice','video'}:raise Rejected('exact_call_type_required')
 return dict(recipient=recipient,kind=kind)
def review(intent):
 recipient=intent['recipient'];d=recipient['destination'];name=d['label']+(' ('+d['phone']+')' if d.get('phone') else ' [verified current directory selection]')
 if 'text' in intent:return 'Send this exact WhatsApp message to '+name+': '+intent['text']+'. Say confirm to send, or cancel.'
 return 'Start a WhatsApp '+intent['kind']+' call to '+name+'. Say confirm to call, or cancel.'

ORDINALS={'first':1,'second':2,'third':3,'fourth':4,'fifth':5,'sixth':6,'seventh':7,'eighth':8,'1st':1,'2nd':2,'3rd':3,'4th':4,'5th':5,'6th':6,'7th':7,'8th':8}
def inspection_choice(request,offered,selection_id,reference_text=None):
 """Bind Onit's selection to a current unquoted name/position reference.

 Onit understands the request. This validates its actual selected ID against
 the current owner's literal reference and offered list, without requiring a
 standalone verb/selection sentence. Inspection never authorizes sending.
 """
 if not isinstance(request,str) or not re.fullmatch('[0-9a-f]{32}',selection_id or '') or not isinstance(offered,list) or not 1<=len(offered)<=8:raise Rejected('fresh_owned_recipient_selection_required')
 exact_text(request,4000)
 reference_text=request if reference_text is None else reference_text
 exact_text(reference_text,200);r=norm(request);ref=norm(reference_text)
 if re.search(r"\b(?:not|never|without|instead of|if i|when i|example|quote|said|says|told)\b|don['’]?t",r):raise Rejected('unambiguous_contact_selection_required')
 # Quoted/message/source words do not become contact-choice evidence.
 unquoted=re.sub(r'"[^"\n]*"|“[^”\n]*”|(?<!\w)\'[^\'\n]*\'(?!\w)',' ',request)
 visible=norm(unquoted)
 if not re.search(r'(?<!\w)'+re.escape(ref)+r'(?!\w)',visible):raise Rejected('selection_reference_not_in_direct_owner_text')
 selected=[x for x in offered if x.get('selection_id')==selection_id]
 if len(selected)!=1:raise Rejected('fresh_owned_recipient_selection_required')
 row=selected[0];positions=[ORDINALS[t] for t in re.findall(r'[\w+]+',ref) if t in ORDINALS]
 numbers={'one':1,'two':2,'three':3,'four':4,'five':5,'six':6,'seven':7,'eight':8}
 for m in re.finditer(r'\b(?:number|option|contact|entry)\s+(one|two|three|four|five|six|seven|eight|[1-8])\b',ref):positions.append(int(m[1]) if m[1].isdigit() else numbers[m[1]])
 if re.fullmatch('[1-8]',ref):positions.append(int(ref))
 if len(positions)>1 or positions and (positions[0]!=row.get('position') or positions[0]>len(offered)):raise Rejected('unambiguous_matching_position_required')
 def named(text):
  found=[x for x in offered if re.search(r'(?<!\w)'+re.escape(norm(x['label']))+r'(?!\w)',text)]
  # The longer exact offered name distinguishes CONTACT_LONG_LABEL from CONTACT_LABEL.
  return [x for x in found if not any(norm(x['label'])!=norm(y['label']) and norm(x['label']) in norm(y['label']) for y in found)]
 names=named(ref);whole=named(visible)
 if any(norm(x['label'])!=norm(row['label']) for x in names+whole):raise Rejected('offered_name_and_position_disagree')
 phone=bool(row.get('phone') and norm(row['phone'])==ref)
 if not positions and not phone and (len(names)!=1 or names[0]['selection_id']!=selection_id):raise Rejected('specific_offered_name_or_position_required')
 return row

def selection_references(request,offered):
 """Literal current-turn candidates for a schema enum, not user intent routing.

 The LLM chooses the tool/person. The host supplies spellings actually present
 now so copying a reference is not another probabilistic generation task.
 The ordinary ownership/quote/conflict/expiry checks still run before RPC.
 """
 if not isinstance(request,str):return []
 text=norm(request);values=[]
 for token in ORDINALS:
  if re.search(r'(?<!\w)'+re.escape(token)+r'(?!\w)',text):values.append(token)
 values.extend(m[0] for m in re.finditer(r'\b(?:number|option|contact|entry)\s+(?:one|two|three|four|five|six|seven|eight|[1-8])\b',text))
 values.extend(m[0] for m in re.finditer(r'(?<!\w)[1-8](?!\w)',text))
 for row in offered:
  label=norm(row['label'])
  if re.search(r'(?<!\w)'+re.escape(label)+r'(?!\w)',text):values.append(label)
 valid=[]
 for value in dict.fromkeys(values):
  for row in offered:
   try:inspection_choice(request,offered,row['selection_id'],value)
   except Rejected:continue
   valid.append(value);break
 return valid[:16]
