"""Separate monotonic message accounting under source-bound standing authority."""
import json,math,time
from pathlib import Path
from benchmark_m6.storage import atomic,read
from me3_assistant.guard import lock
from me3_assistant.library import plain_file
def authorized(grant):return grant.get('profile')=='standing' and ((grant.get('routine_whatsapp_messages') is True and grant.get('whatsapp_recipient_scope')=='self' and 'propose_whatsapp_message' in grant.get('allowed_actions',[])) or (grant.get('routine_whatsapp_contact_messages') is True and grant.get('whatsapp_recipient_scope')=='verified_contacts' and 'propose_contact_message' in grant.get('allowed_actions',[]))) and (grant.get('workspace_access') is False or (grant.get('workspace_access') is True and grant.get('workspace_scope')=='pi_artifacts_only'))
def validate(data):
 if not isinstance(data,dict) or set(data)!={'version','message_send','attempts'} or data['version']!=1 or type(data['message_send']) is not int or data['message_send']<0 or not isinstance(data['attempts'],list) or len(data['attempts'])>6 or any(type(n) not in {int,float} or not math.isfinite(n) or n<0 for n in data['attempts']):raise ValueError('invalid_standing_message_counter')
 return data
def initialize(path,legacy_dispatches=0):
 path=Path(path)
 if type(legacy_dispatches) is not int or legacy_dispatches<0:raise ValueError('invalid_message_history')
 path.parent.mkdir(parents=True,exist_ok=True)
 if path.is_symlink() or path.exists() and not plain_file(path.lstat()):raise ValueError('untrusted_message_counter')
 with lock(path.with_suffix('.lock')):
  if path.exists():
   data=validate(read(path))
   if data['message_send']<legacy_dispatches:raise ValueError('message_counter_below_preserved_history')
   return data
  with path.open('x',encoding='utf-8') as f:json.dump(dict(version=1,message_send=legacy_dispatches,attempts=[]),f)
  return validate(read(path))
class StandingMessageBudget:
 def __init__(self,base,grant,path,clock=time.time):
  if not authorized(grant):raise ValueError('standing_self_message_not_authorized')
  self.base=base;self.grant=grant;self.path=Path(path);self.clock=clock;self.observed=0
 def _state(self):
  self.base.permitted('asr')
  if self.path.is_symlink() or not self.path.is_file() or not plain_file(self.path.lstat()) or self.path.stat().st_size>16384:raise ValueError('standing_message_counter_missing')
  data=validate(read(self.path));now=self.clock()
  if type(now) not in {int,float} or not math.isfinite(now) or now<0 or any(t>now+1 for t in data['attempts']):raise ValueError('invalid_message_clock')
  if data['message_send']<self.observed:raise ValueError('standing_message_counter_decreased')
  self.observed=data['message_send'];return data,[t for t in data['attempts'] if now-t<60]
 def _used(self):return self._state()[0]['message_send']
 def permitted(self,role):return len(self._state()[1])<6 if role=='message_send' else self.base.permitted(role)
 def reserve(self,role):
  if role!='message_send':return self.base.reserve(role)
  p=self.path.with_suffix('.lock')
  if p.is_symlink() or p.exists() and not plain_file(p.lstat()):raise ValueError('untrusted_message_counter_lock')
  with lock(p):
   data,attempts=self._state()
   if len(attempts)>=6:raise ValueError('standing_message_rate_limited')
   data['message_send']+=1;data['attempts']=attempts+[self.clock()];atomic(self.path,data);self.observed=data['message_send'];return self.observed
