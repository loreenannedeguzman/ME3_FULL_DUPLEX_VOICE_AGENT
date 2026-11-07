"""Separate monotonic reservations for separately confirmed Gmail sends."""
import math,time
from pathlib import Path
from benchmark_m6.storage import read
from me3_assistant.guard import lock
from me3_productivity.budget import safe,private_lock,atomic_private

def authorized(grant):
    return (grant.get('profile')=='standing' and grant.get('mode') in {'normal','text'}
            and grant.get('routine_gmail_messages') is True
            and grant.get('gmail_scope')=='commanded_reads_separately_confirmed_send'
            and 'propose_gmail_send' in grant.get('allowed_actions',[]))

def validate(value):
    if (not isinstance(value,dict) or set(value)!={'version','gmail_send','attempts'} or type(value.get('version')) is not int or value['version']!=1
        or type(value.get('gmail_send')) is not int or value['gmail_send']<0 or not isinstance(value.get('attempts'),list) or len(value['attempts'])>6
        or any(type(t) not in (int,float) or not math.isfinite(t) or t<0 for t in value['attempts'])):raise ValueError('invalid_monotonic_gmail_counter')
    return value

def initialize(path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);private_lock(path.with_suffix('.lock'))
    with lock(path.with_suffix('.lock')):
        if path.exists():safe(path);return validate(read(path))
        safe(path,True);atomic_private(path,dict(version=1,gmail_send=0,attempts=[]))
        return validate(read(path))

class StandingGmailBudget:
    def __init__(self,base,grant,path,clock=time.time):
        if not authorized(grant):raise ValueError('standing_gmail_not_authorized')
        self.base=base;self.path=Path(path);self.clock=clock;self.observed=0
    def state(self):
        self.base.permitted('asr')
        safe(self.path);value=validate(read(self.path));now=self.clock()
        if type(now) not in (int,float) or not math.isfinite(now) or now<0 or any(t>now+1 for t in value['attempts']):raise ValueError('gmail_clock_reversed')
        if value['gmail_send']<self.observed:raise ValueError('gmail_counter_decreased')
        self.observed=value['gmail_send'];return value,[t for t in value['attempts'] if now-t<60],now
    def permitted(self,role):
        return len(self.state()[1])<6 if role=='gmail_send' else self.base.permitted(role)
    def reserve(self,role):
        if role!='gmail_send':return self.base.reserve(role)
        private_lock(self.path.with_suffix('.lock'))
        with lock(self.path.with_suffix('.lock')):
            value,recent,now=self.state()
            if len(recent)>=6:raise ValueError('gmail_rate_limited_no_retry')
            value['gmail_send']+=1;value['attempts']=recent+[now]
            atomic_private(self.path,value);self.observed=value['gmail_send'];return self.observed
