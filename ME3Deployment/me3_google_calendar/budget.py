"""Independent monotonic Calendar RPC-attempt accounting, never model quota."""
import math,time
from pathlib import Path
from benchmark_m6.storage import read
from me3_assistant.guard import lock
from me3_productivity.budget import safe,private_lock,atomic_private

ACCOUNT='owner@example.invalid'
SCOPE='commanded_primary_basic_events_confirmed_mutations'
LIMITS={'google_calendar_read':30,'google_calendar_control':6}

def authorized(grant):
 return (grant.get('profile')=='standing' and grant.get('mode') in {'normal','text'}
  and grant.get('routine_google_calendar') is True and grant.get('google_calendar_account')==ACCOUNT
  and grant.get('google_calendar_id')=='primary' and grant.get('google_calendar_scope')==SCOPE
  and 'propose_google_calendar' in grant.get('allowed_actions',[]))

def validate_configuration(policy,config):
 servers=[s for s in config['servers'] if s['module']=='me3_google_calendar.server']
 actions=[a for a in config['actions'] if a['name']=='propose_google_calendar']
 if (policy.get('google_calendar_account')!=ACCOUNT or policy.get('google_calendar_id')!='primary'
  or policy.get('google_calendar_scope')!=SCOPE or len(servers)!=1 or len(actions)!=1
  or actions[0]!=dict(name='propose_google_calendar',server=servers[0]['id'],adapter='google_calendar_control')
  or servers[0]['identity']!='me3' or servers[0]['role']!='utility' or servers[0]['args']!=[]
  or servers[0]['read_tools']!=['google_calendar_context','list_google_calendar_events','read_google_calendar_event']):
  raise ValueError('fixed_primary_google_calendar_authority_required')

def validate(v):
 if (not isinstance(v,dict) or set(v)!={'version',*LIMITS,'attempts'} or type(v['version']) is not int or v['version']!=1
  or any(type(v[r]) is not int or v[r]<0 for r in LIMITS) or not isinstance(v['attempts'],list) or len(v['attempts'])>36):
  raise ValueError('invalid_monotonic_calendar_counter')
 for row in v['attempts']:
  if (not isinstance(row,dict) or set(row)!={'role','at'} or row['role'] not in LIMITS
   or type(row['at']) not in (int,float) or not math.isfinite(row['at']) or row['at']<0):raise ValueError('invalid_calendar_rate_history')
 return v

def initialize(path):
 """Explicit activation only; an existing book is validated, never reset."""
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);private_lock(path.with_suffix('.lock'))
 with lock(path.with_suffix('.lock')):
  if path.exists():safe(path);return validate(read(path))
  safe(path,True);atomic_private(path,dict(version=1,google_calendar_read=0,google_calendar_control=0,attempts=[]))
  return validate(read(path))

class StandingCalendarBudget:
 def __init__(self,base,grant,path,clock=time.time):
  if not authorized(grant):raise ValueError('standing_calendar_not_authorized')
  self.base=base;self.path=Path(path);self.clock=clock;self.observed={r:0 for r in LIMITS}
 def state(self):
  self.base.permitted('asr')
  safe(self.path);v=validate(read(self.path));now=self.clock()
  if type(now) not in (int,float) or not math.isfinite(now) or now<0 or any(r['at']>now+1 for r in v['attempts']):raise ValueError('calendar_clock_reversed')
  if any(v[r]<self.observed[r] for r in LIMITS):raise ValueError('calendar_counter_decreased')
  self.observed={r:v[r] for r in LIMITS};return v,[r for r in v['attempts'] if now-r['at']<60],now
 def permitted(self,role):
  if role not in LIMITS:return self.base.permitted(role)
  return sum(r['role']==role for r in self.state()[1])<LIMITS[role]
 def reserve(self,role):
  if role not in LIMITS:return self.base.reserve(role)
  private_lock(self.path.with_suffix('.lock'))
  with lock(self.path.with_suffix('.lock')):
   v,recent,now=self.state()
   if sum(r['role']==role for r in recent)>=LIMITS[role]:raise ValueError('calendar_rate_limited_no_retry')
   v[role]+=1;v['attempts']=recent+[dict(role=role,at=now)];atomic_private(self.path,v);self.observed={r:v[r] for r in LIMITS};return v[role]
