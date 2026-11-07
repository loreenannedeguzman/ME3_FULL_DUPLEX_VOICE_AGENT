"""Versioned local productivity records and once-only reviewed operations.

No network, model, shell, scheduler, notification or destructive-delete API.
Every read opens a new SQLite connection; committed snapshots remain immutable.
"""
import hashlib,hmac,json,math,os,re,secrets,sqlite3,stat,time,uuid
from contextlib import closing,contextmanager
from datetime import date,datetime,timezone
from pathlib import Path
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError

DEFAULT_TIMEZONE='Asia/Manila'
READ_SOURCE='fresh_productivity_sqlite_connection'
MAX_STORE_BYTES=64*1024*1024
FIELDS={'title','body','due_date','due_at','start_at','end_at','timezone','clear_due'}
COMMANDS={'task':{'create','update','complete','reopen','cancel'},'event':{'create','update','cancel'},'note':{'create','update','archive'}}

def canonical(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def digest(v):return hashlib.sha256(canonical(v).encode()).hexdigest()
def identifier(v):
 if not isinstance(v,str) or re.fullmatch('[0-9a-f]{32}',v) is None:raise ValueError('exact_productivity_id_required')
 return v
def revision(v):
 if type(v) is not int or not 1<=v<=2147483647:raise ValueError('exact_current_revision_required')
 return v
def text(v,limit,*,empty=False):
 if not isinstance(v,str) or len(v)>limit or not empty and (v!=v.strip() or not v) or any(ord(c)<32 and c not in '\n\t' for c in v):raise ValueError('bounded_exact_text_required')
 return v
def zone(v):
 if not isinstance(v,str) or not 1<=len(v)<=80 or re.fullmatch(r'[A-Za-z0-9_+\-/]+',v) is None:raise ValueError('explicit_timezone_required')
 try:return ZoneInfo(v)
 except ZoneInfoNotFoundError:raise ValueError('timezone_unavailable') from None
def instant(v,tz):
 if not isinstance(v,str) or len(v)>48 or 'T' not in v:raise ValueError('explicit_offset_datetime_required')
 try:d=datetime.fromisoformat(v.replace('Z','+00:00'))
 except ValueError:raise ValueError('explicit_offset_datetime_required') from None
 if d.tzinfo is None or d.utcoffset() is None or d.microsecond:raise ValueError('explicit_offset_datetime_required')
 local=d.astimezone(zone(tz))
 if local.replace(tzinfo=None)!=d.replace(tzinfo=None) or local.utcoffset()!=d.utcoffset():raise ValueError('timezone_offset_or_nonexistent_time_mismatch')
 return d.isoformat(timespec='seconds'),d.timestamp()
def date_only(v):
 if not isinstance(v,str) or re.fullmatch(r'\d{4}-\d{2}-\d{2}',v) is None:raise ValueError('explicit_calendar_date_required')
 try:return date.fromisoformat(v).isoformat()
 except ValueError:raise ValueError('explicit_calendar_date_required') from None
def page_args(limit,offset):
 if type(limit) is not int or not 1<=limit<=25 or type(offset) is not int or not 0<=offset<=10000:raise ValueError('bounded_productivity_page_required')
def validate_fields(kind,command,data):
 """Pure input check; no language parsing, inference, IO or proposed effect."""
 if kind not in COMMANDS or command not in COMMANDS[kind] or not isinstance(data,dict) or set(data)-FIELDS:raise ValueError('unsupported_productivity_change')
 if len(json.dumps(data))>7000:raise ValueError('productivity_edit_transport_bound')
 if command not in {'create','update'} and data:raise ValueError('state_change_has_no_edit_fields')
 allowed={'title','body'}|({'due_date','due_at','timezone','clear_due'} if kind=='task' else {'start_at','end_at','timezone'} if kind=='event' else set())
 if set(data)-allowed:raise ValueError('fields_do_not_match_record_kind')
 for name,limit in [('title',200),('body',6000)]:
  if name in data:text(data[name],limit,empty=name=='body')
 if command=='create' and ('title' not in data or kind=='note' and not data.get('body','').strip()):raise ValueError('title_and_note_content_required')
 if command=='update' and not data:raise ValueError('explicit_changes_required')
 if 'clear_due' in data and type(data['clear_due']) is not bool:raise ValueError('clear_due_must_be_boolean')
 if data.get('clear_due') and any(data.get(k) is not None for k in ('due_date','due_at')):raise ValueError('choose_due_date_or_instant_or_clear')
 if data.get('due_date') is not None and data.get('due_at') is not None:raise ValueError('choose_due_date_or_instant_or_clear')
 if data.get('due_date') is not None:date_only(data['due_date'])
 if data.get('timezone') is not None:zone(data['timezone'])
 if command=='create' and kind=='event' and not all(data.get(k) for k in ('start_at','end_at')):raise ValueError('explicit_event_start_and_end_required')
 for key in ('due_at','start_at','end_at'):
  if data.get(key) is not None:
   # An update may use its existing timezone; full zone consistency is checked
   # against the persisted row when the exact target revision is prepared.
   if data.get('timezone') is not None or command=='create':instant(data[key],data.get('timezone') or DEFAULT_TIMEZONE)
   else:
    try:d=datetime.fromisoformat(data[key].replace('Z','+00:00'))
    except (AttributeError,ValueError):raise ValueError('explicit_offset_datetime_required') from None
    if d.tzinfo is None or d.utcoffset() is None:raise ValueError('explicit_offset_datetime_required')
 return data

class Store:
 def __init__(self,root,path=None,*,clock=time.time):
  self.root=Path(root).absolute();self.path=Path(path or self.root/'data/productivity.sqlite3').absolute();self.clock=clock
  if self.root!=self.root.resolve() or not self.path.is_relative_to(self.root):raise ValueError('project_local_productivity_store_required')
 def _safe(self,*,missing=False):
  if not self.path.resolve().is_relative_to(self.root):raise ValueError('project_local_productivity_store_required')
  for p in [self.path,*self.path.parents]:
   if p.is_symlink():raise ValueError('private_productivity_path_required')
   if p==self.root:break
  if self.path.exists():
   s=self.path.stat()
   if not stat.S_ISREG(s.st_mode) or s.st_nlink!=1 or s.st_size>MAX_STORE_BYTES:raise ValueError('private_bounded_productivity_store_required')
   if os.name!='nt' and (s.st_uid!=os.getuid() or stat.S_IMODE(s.st_mode)!=0o600):raise ValueError('private_productivity_mode_required')
  elif not missing:raise ValueError('productivity_store_not_initialized')
 def now(self):
  n=self.clock()
  if type(n) not in {int,float} or not math.isfinite(n) or n<0:raise ValueError('productivity_clock_required')
  return n
 def initialize(self):
  self._safe(missing=True);self.path.parent.mkdir(parents=True,exist_ok=True)
  fd=os.open(self.path,os.O_CREAT|os.O_RDWR|getattr(os,'O_NOFOLLOW',0),0o600);os.close(fd);self._safe()
  with self.connect() as c:c.executescript('''
   CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY,kind TEXT NOT NULL,revision INTEGER NOT NULL,state TEXT NOT NULL,title TEXT NOT NULL,body TEXT NOT NULL,due_date TEXT,due_at TEXT,start_at TEXT,end_at TEXT,timezone TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
   CREATE TABLE IF NOT EXISTS proposals(id TEXT PRIMARY KEY,owner TEXT NOT NULL,token_hash TEXT NOT NULL,intent TEXT NOT NULL,digest TEXT NOT NULL,created REAL NOT NULL,expires REAL NOT NULL);
   CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY,target TEXT NOT NULL,revision INTEGER NOT NULL,digest TEXT NOT NULL,intent TEXT NOT NULL,snapshot TEXT NOT NULL,token_hash TEXT NOT NULL,committed REAL NOT NULL);
   CREATE INDEX IF NOT EXISTS record_kind ON records(kind,state,id);
  ''')
 @contextmanager
 def connect(self,read_only=False):
  self._safe()
  with closing(sqlite3.connect(self.path.resolve().as_uri()+('?mode=ro' if read_only else '?mode=rw'),uri=True,timeout=2)) as c:
   c.row_factory=sqlite3.Row
   if not read_only:c.execute('PRAGMA synchronous=FULL')
   with c:yield c
 def context(self):
  with self.connect(True) as c:c.execute('SELECT 1 FROM records LIMIT 1').fetchone()
  n=self.now();local=datetime.fromtimestamp(n,zone(DEFAULT_TIMEZONE))
  return dict(available=True,default_timezone=DEFAULT_TIMEZONE,clock_utc=datetime.fromtimestamp(n,timezone.utc).isoformat(timespec='seconds'),local_now=local.isoformat(timespec='seconds'),local_date=local.date().isoformat(),calendar='Pi-local entries only; not synced to Google/Outlook and no invitations or alarms.',mutations='Exact review and separate confirmation; state changes preserve records. Explicit IDs and current revisions prevent stale edits.',read_only=True,verification_source=READ_SOURCE)
 def _row(self,c,target,kind=None):
  row=c.execute('SELECT * FROM records WHERE id=?',(identifier(target),)).fetchone()
  if row is None or kind is not None and row['kind']!=kind:raise ValueError('productivity_record_not_found')
  return dict(row)
 def read(self,target,kind=None,*,body_offset=0,max_body_chars=800,expected_revision=None):
  if type(body_offset) is not int or body_offset<0 or type(max_body_chars) is not int or not 1<=max_body_chars<=800:raise ValueError('bounded_body_window_required')
  if body_offset and expected_revision is None:raise ValueError('continuation_requires_expected_revision')
  with self.connect(True) as c:row=self._row(c,target,kind)
  if expected_revision is not None and row['revision']!=revision(expected_revision):raise ValueError('record_changed_read_fresh_version')
  body=row['body']
  if body_offset>len(body):raise ValueError('body_offset_out_of_range')
  row['body']=body[body_offset:body_offset+max_body_chars];next_offset=body_offset+len(row['body'])
  return dict(row=row,expected_revision=row['revision'],body_offset=body_offset,body_complete=next_offset>=len(body),next_body_offset=next_offset if next_offset<len(body) else None,body_characters=len(body),body_sha256=hashlib.sha256(body.encode()).hexdigest(),read_only=True,verification_source=READ_SOURCE)
 def _normalize(self,kind,command,data,before,target,now):
  validate_fields(kind,command,data)
  if before is None:
   row=dict(id=target,kind=kind,revision=1,state={'task':'open','event':'scheduled','note':'active'}[kind],title=data['title'],body=data.get('body',''),due_date=None,due_at=None,start_at=None,end_at=None,timezone=None,created_at=now,updated_at=now)
  else:
   row=dict(before);row['revision']+=1;row['updated_at']=now
  for key,value in data.items():
   if key!='clear_due' and value is not None:row[key]=value
  if kind=='task':
   if data.get('clear_due'):row['due_date']=row['due_at']=None
   if data.get('due_date') is not None:row['due_at']=None
   if data.get('due_at') is not None:row['due_date']=None
   if row['due_date'] is not None:row['due_date']=date_only(row['due_date'])
   if row['due_at'] is not None:row['due_at'],_=instant(row['due_at'],row.get('timezone') or DEFAULT_TIMEZONE)
   row['timezone']=row.get('timezone') or DEFAULT_TIMEZONE if row['due_date'] or row['due_at'] else None
  if kind=='event':
   row['timezone']=row.get('timezone') or DEFAULT_TIMEZONE
   row['start_at'],start=instant(row['start_at'],row['timezone']);row['end_at'],end=instant(row['end_at'],row['timezone'])
   if not 0<end-start<=31*86400:raise ValueError('event_range_must_be_positive_and_at_most_31_days')
  states={'complete':('open','completed'),'reopen':('completed','open'),'cancel':('open' if kind=='task' else 'scheduled','canceled'),'archive':('active','archived')}
  if command in states:
   old,new=states[command]
   if before is None or before['state']!=old:raise ValueError('record_state_changed_or_already_terminal')
   row['state']=new
  if before is not None and before['state'] in {'canceled','archived'}:raise ValueError('terminal_record_cannot_be_edited')
  if before is not None and all(row[k]==before[k] for k in row if k not in {'revision','updated_at'}):raise ValueError('change_would_not_modify_record')
  return row
 def prepare(self,operation_id,owner,kind,command,data,target_id=None,expected_revision=None):
  identifier(operation_id);identifier(owner);validate_fields(kind,command,data)
  now=self.now();stamp=datetime.fromtimestamp(now,timezone.utc).isoformat(timespec='seconds')
  with self.connect() as c:
   c.execute('BEGIN IMMEDIATE');before=None
   if command=='create':
    if target_id is not None or expected_revision is not None:raise ValueError('create_requires_no_target_or_revision')
    target=operation_id
   else:
    target=identifier(target_id);before=self._row(c,target,kind)
    if before['revision']!=revision(expected_revision):raise ValueError('stale_productivity_revision')
   after=self._normalize(kind,command,data,before,target,stamp)
   intent=dict(kind=kind,command=command,target_id=target,expected_revision=before['revision'] if before else None,before_digest=digest(before) if before else None,after=after)
   expected=digest(intent);old=c.execute('SELECT * FROM proposals WHERE id=?',(operation_id,)).fetchone()
   if old:
    # Never silently replace or extend a reviewed immutable proposal.
    if old['owner']!=owner or old['digest']!=expected:raise ValueError('proposal_identity_changed')
    raise ValueError('proposal_already_prepared_no_replay')
   token=secrets.token_hex(32)
   c.execute('INSERT INTO proposals VALUES(?,?,?,?,?,?,?)',(operation_id,owner,hashlib.sha256(token.encode()).hexdigest(),canonical(intent),expected,now,now+120))
  return dict(operation_id=operation_id,intent=intent,expected_digest=expected,token=token,expires_in_seconds=120)
 def commit(self,operation_id,owner,token,confirmation):
  identifier(operation_id);identifier(owner)
  if confirmation!='confirm' or not isinstance(token,str) or re.fullmatch('[0-9a-f]{64}',token) is None:raise ValueError('separate_exact_confirmation_required')
  token_hash=hashlib.sha256(token.encode()).hexdigest();now=self.now()
  with self.connect() as c:
   c.execute('BEGIN IMMEDIATE');old=c.execute('SELECT * FROM operations WHERE id=?',(operation_id,)).fetchone()
   if old:
    prepared_owner=c.execute('SELECT owner FROM proposals WHERE id=?',(operation_id,)).fetchone()
    if prepared_owner is None or prepared_owner['owner']!=owner:raise ValueError('operation_owner_mismatch')
    if not hmac.compare_digest(old['token_hash'],token_hash):raise ValueError('operation_token_mismatch')
    return dict(operation_id=operation_id,expected_digest=old['digest'],target_id=old['target'],revision=old['revision'],duplicate=True)
   proposal=c.execute('SELECT * FROM proposals WHERE id=?',(operation_id,)).fetchone()
   if (proposal is None or proposal['owner']!=owner or not hmac.compare_digest(proposal['token_hash'],token_hash)
       or not proposal['created']<=now<proposal['expires']):raise ValueError('proposal_missing_expired_or_foreign')
   intent=json.loads(proposal['intent']);row=intent['after'];target=intent['target_id']
   fields={'id','kind','revision','state','title','body','due_date','due_at','start_at','end_at','timezone','created_at','updated_at'}
   if digest(intent)!=proposal['digest'] or set(row)!=fields or row['id']!=target or row['kind']!=intent['kind']:raise ValueError('immutable_proposal_corrupted')
   if intent['command']=='create':
    if c.execute('SELECT 1 FROM records WHERE id=?',(target,)).fetchone():raise ValueError('create_target_already_exists')
    names=tuple(row)
    c.execute('INSERT INTO records('+','.join(names)+') VALUES('+','.join('?' for _ in names)+')',tuple(row[k] for k in names))
   else:
    current=self._row(c,target,intent['kind'])
    if current['revision']!=intent['expected_revision'] or digest(current)!=intent['before_digest']:raise ValueError('stale_productivity_revision')
    names=[k for k in row if k!='id']
    if c.execute('UPDATE records SET '+','.join(k+'=?' for k in names)+' WHERE id=? AND revision=?',(*[row[k] for k in names],target,intent['expected_revision'])).rowcount!=1:raise ValueError('concurrent_productivity_change')
   c.execute('INSERT INTO operations VALUES(?,?,?,?,?,?,?,?)',(operation_id,target,row['revision'],proposal['digest'],proposal['intent'],canonical(row),token_hash,now))
  return dict(operation_id=operation_id,expected_digest=proposal['digest'],target_id=target,revision=row['revision'],duplicate=False)
 def verify(self,operation_id):
  with self.connect(True) as c:
   c.execute('BEGIN')
   op=c.execute('SELECT * FROM operations WHERE id=?',(identifier(operation_id),)).fetchone()
   if op is None:return dict(operation_id=operation_id,row=None,verification_source=READ_SOURCE)
   current=self._row(c,op['target']);snapshot=json.loads(op['snapshot']);intent=json.loads(op['intent'])
   if digest(intent)!=op['digest'] or snapshot!=intent['after'] or current['revision']<op['revision']:raise ValueError('operation_readback_corrupted')
   if current['revision']==op['revision'] and current!=snapshot:raise ValueError('current_readback_changed_without_revision')
   return dict(operation_id=operation_id,expected_digest=op['digest'],intent_digest=digest(intent),target_id=op['target'],kind=intent['kind'],command=intent['command'],
     applied_revision=op['revision'],applied_after_digest=digest(snapshot),applied_state=snapshot['state'],current_revision=current['revision'],current_after_digest=digest(current),
     superseded=current['revision']>op['revision'],verification_source=READ_SOURCE,read_only=True)
 def listing(self,kind,*,status=None,start_at=None,end_at=None,timezone_name=DEFAULT_TIMEZONE,include_terminal=False,query=None,limit=20,offset=0):
  page_args(limit,offset)
  if kind not in COMMANDS or type(include_terminal) is not bool:raise ValueError('invalid_productivity_listing')
  sql='kind=?';params=[kind]
  if kind=='task':
   if status not in {'open','completed','canceled','all'}:raise ValueError('explicit_task_status_required')
   if status!='all':sql+=' AND state=?';params.append(status)
  elif not include_terminal:sql+=' AND state=?';params.append('scheduled' if kind=='event' else 'active')
  if kind=='event':
   _,start=instant(start_at,timezone_name);_,end=instant(end_at,timezone_name)
   if not 0<end-start<=366*86400:raise ValueError('calendar_read_range_required_maximum_366_days')
  if query is not None:
   text(query,200);escaped=query.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
   sql+=" AND (title LIKE ? ESCAPE '\\' OR body LIKE ? ESCAPE '\\')";params.extend(['%'+escaped+'%']*2)
  with self.connect(True) as c:
   rows=[dict(r) for r in c.execute('SELECT * FROM records WHERE '+sql+' ORDER BY created_at,id',params)]
  if kind=='event':rows=[r for r in rows if datetime.fromisoformat(r['start_at']).timestamp()<end and datetime.fromisoformat(r['end_at']).timestamp()>start];rows.sort(key=lambda r:(datetime.fromisoformat(r['start_at']).timestamp(),r['id']))
  total=len(rows);part=[]
  for row in rows[offset:offset+limit]:
   row=dict(row);body=row.pop('body');row['body_excerpt']=body[:120];row['body_characters']=len(body)
   if len(json.dumps(part+[row]))>10000:break
   part.append(row)
  if offset<total and not part:raise ValueError('record_summary_exceeds_transport_bound')
  return {kind+'s' if kind!='note' else 'notes':part,'total':total,'offset':offset,'next_offset':offset+len(part) if offset+len(part)<total else None,'read_only':True,'verification_source':READ_SOURCE}
