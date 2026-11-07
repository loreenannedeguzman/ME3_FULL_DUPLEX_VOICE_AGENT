"""Separate monotonic app-window counter; historical usage is never reset."""
import json,math,time
from pathlib import Path
from benchmark_m6.storage import atomic,read
from me3_assistant.guard import lock
from me3_assistant.library import plain_file
def authorized(g):return g.get('profile')=='standing' and g.get('routine_whatsapp_window') is True and 'propose_whatsapp_window' in g.get('allowed_actions',[]) and (g.get('workspace_access') is False or (g.get('workspace_access') is True and g.get('workspace_scope')=='pi_artifacts_only'))
def validate(v):
 if not isinstance(v,dict) or set(v)!={'version','window_control','attempts'} or v['version']!=1 or type(v['window_control']) is not int or v['window_control']<0 or not isinstance(v['attempts'],list) or len(v['attempts'])>10 or any(type(t) not in {int,float} or not math.isfinite(t) or t<0 for t in v['attempts']):raise ValueError('invalid_window_counter')
 return v
def initialize(path):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 if path.is_symlink() or path.exists() and not plain_file(path.lstat()):raise ValueError('untrusted_window_counter')
 with lock(path.with_suffix('.lock')):
  if path.exists():return validate(read(path))
  with path.open('x',encoding='utf-8') as f:json.dump(dict(version=1,window_control=0,attempts=[]),f)
  return validate(read(path))
class StandingWindowBudget:
 def __init__(self,base,grant,path,clock=time.time):
  if not authorized(grant):raise ValueError('window_control_not_authorized')
  self.base=base;self.path=Path(path);self.clock=clock;self.observed=0
 def _state(self):
  self.base.permitted('asr')
  if self.path.is_symlink() or not self.path.is_file() or not plain_file(self.path.lstat()) or self.path.stat().st_size>16384:raise ValueError('window_counter_missing')
  value=validate(read(self.path));now=self.clock()
  if not math.isfinite(now) or any(t>now+1 for t in value['attempts']) or value['window_control']<self.observed:raise ValueError('window_counter_or_clock_invalid')
  self.observed=value['window_control'];return value,[t for t in value['attempts'] if now-t<60]
 def permitted(self,role):return len(self._state()[1])<10 if role=='window_control' else self.base.permitted(role)
 def reserve(self,role):
  if role!='window_control':return self.base.reserve(role)
  with lock(self.path.with_suffix('.lock')):
   value,times=self._state()
   if len(times)>=10:raise ValueError('window_rate_limited')
   value['window_control']+=1;value['attempts']=times+[self.clock()];atomic(self.path,value);self.observed=value['window_control'];return self.observed
