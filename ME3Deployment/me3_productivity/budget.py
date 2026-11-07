"""Separate monotonic routine local-productivity/reminder reservations."""
import json,math,os,secrets,stat,time
from pathlib import Path
from benchmark_m6.storage import read
from me3_assistant.guard import lock
ROLES={'productivity_control','reminder_control'}
def roles(grant):
 if grant.get('profile')!='standing' or grant.get('mode') not in {'normal','text'}:return set()
 result=set()
 if grant.get('routine_productivity') is True and grant.get('productivity_scope')=='pi_local_tasks_events_notes' and 'propose_productivity' in grant.get('allowed_actions',[]):result.add('productivity_control')
 if grant.get('routine_local_reminders') is True and 'propose_reminder' in grant.get('allowed_actions',[]):result.add('reminder_control')
 return result
def authorized(grant):return bool(roles(grant))
def validate(v):
 if (not isinstance(v,dict) or set(v)!={'version',*ROLES,'attempts'} or type(v.get('version')) is not int or v['version']!=1
     or any(type(v.get(r)) is not int or v[r]<0 for r in ROLES) or not isinstance(v.get('attempts'),list) or len(v['attempts'])>30):raise ValueError('invalid_monotonic_productivity_counter')
 for row in v['attempts']:
  if (not isinstance(row,dict) or set(row)!={'role','at'} or row['role'] not in ROLES
      or type(row['at']) not in {int,float} or not math.isfinite(row['at']) or row['at']<0):raise ValueError('invalid_productivity_rate_history')
 return v
def safe(path,missing=False):
 path=Path(path)
 if path.is_symlink() or any(p.is_symlink() for p in path.parents):raise ValueError('private_productivity_counter_required')
 if path.exists():
  s=path.stat()
  if not stat.S_ISREG(s.st_mode) or s.st_nlink!=1 or s.st_size>16384:raise ValueError('private_productivity_counter_required')
  if os.name!='nt' and (s.st_uid!=os.getuid() or s.st_mode&0o777!=0o600):raise ValueError('private_productivity_counter_required')
 elif not missing:raise ValueError('productivity_counter_not_initialized')
def private_lock(path):
 safe(path,True)
 if not path.exists():
  try:
   fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600);os.close(fd)
  except FileExistsError:pass
 safe(path)
def atomic_private(path,value):
 tmp=path.with_name(path.name+'.'+secrets.token_hex(16)+'.tmp')
 fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
 try:
  with os.fdopen(fd,'w',encoding='utf-8') as f:json.dump(value,f,allow_nan=False);f.flush();os.fsync(f.fileno())
  os.replace(tmp,path)
  if os.name!='nt':
   directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
   try:os.fsync(directory)
   finally:os.close(directory)
 finally:
  if tmp.exists():tmp.unlink()
def initialize(path):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);safe(path,True);private_lock(path.with_suffix('.lock'))
 with lock(path.with_suffix('.lock')):
  if path.exists():return validate(read(path))
  fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
  with os.fdopen(fd,'w',encoding='utf-8') as f:json.dump(dict(version=1,productivity_control=0,reminder_control=0,attempts=[]),f);f.flush();os.fsync(f.fileno())
  if os.name!='nt':
   directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
   try:os.fsync(directory)
   finally:os.close(directory)
  return validate(read(path))
class StandingProductivityBudget:
 def __init__(self,base,grant,path,*,clock=time.time):
  if not authorized(grant):raise ValueError('standing_productivity_not_authorized')
  self.base=base;self.enabled=roles(grant);self.path=Path(path);self.clock=clock;self.observed={r:0 for r in ROLES}
 def state(self):
  self.base.permitted('asr') # Fresh authority/counter validation, no ASR request.
  safe(self.path);v=validate(read(self.path));now=self.clock()
  if type(now) not in {int,float} or not math.isfinite(now) or now<0 or any(row['at']>now+1 for row in v['attempts']):raise ValueError('productivity_clock_reversed')
  if any(v[r]<self.observed[r] for r in ROLES):raise ValueError('productivity_counter_decreased')
  self.observed={r:v[r] for r in ROLES};return v,[r for r in v['attempts'] if now-r['at']<60],now
 def permitted(self,role):
  if role not in ROLES:return self.base.permitted(role)
  return role in self.enabled and len(self.state()[1])<30
 def reserve(self,role):
  if role not in ROLES:return self.base.reserve(role)
  if role not in self.enabled:raise ValueError('productivity_role_not_authorized')
  private_lock(self.path.with_suffix('.lock'))
  with lock(self.path.with_suffix('.lock')):
   v,recent,now=self.state()
   if len(recent)>=30:raise ValueError('productivity_rate_limited_no_retry')
   v[role]+=1;v['attempts']=recent+[dict(role=role,at=now)];atomic_private(self.path,v);self.observed={r:v[r] for r in ROLES};return v[role]
