"""Separate monotonic call reservations. Never resets message/provider counts."""
import json,math,time
from pathlib import Path
from benchmark_m6.storage import atomic,read
from me3_assistant.guard import lock
from me3_assistant.library import plain_file

def authorized(g):
    return (g.get('profile')=='standing' and g.get('mode')=='normal' and g.get('routine_whatsapp_calls') is True and 'propose_whatsapp_call' in g.get('allowed_actions',[]) and g.get('whatsapp_qualification_outbound_only')==[dict(name='CONTACT_LABEL',phone='+12025550123')])
def validate(v):
    if (not isinstance(v,dict) or set(v)!={'version','call_start','attempts'} or v['version']!=1 or type(v['call_start']) is not int or v['call_start']<0 or not isinstance(v['attempts'],list) or len(v['attempts'])>3 or any(type(x) not in (int,float) or not math.isfinite(x) or x<0 for x in v['attempts'])):raise ValueError('invalid_monotonic_call_counter')
    return v
def initialize(path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    if path.is_symlink() or path.exists() and not plain_file(path.lstat()):raise ValueError('private_call_counter_required')
    with lock(path.with_suffix('.lock')):
        if not path.exists():
            with path.open('x',encoding='utf-8') as f:json.dump(dict(version=1,call_start=0,attempts=[]),f)
        return validate(read(path))
class StandingCallBudget:
    def __init__(self,base,grant,path,clock=time.time):
        if not authorized(grant):raise ValueError('normal_GUI_call_policy_required')
        self.base=base;self.path=Path(path);self.clock=clock;self.observed=0
    def _state(self):
        self.base.permitted('asr')
        if self.path.is_symlink() or not self.path.is_file() or not plain_file(self.path.lstat()) or self.path.stat().st_size>16384:raise ValueError('call_counter_missing')
        v=validate(read(self.path));now=self.clock()
        if type(now) not in (int,float) or not math.isfinite(now) or any(t>now+1 for t in v['attempts']) or v['call_start']<self.observed:raise ValueError('call_counter_or_clock_changed')
        self.observed=v['call_start'];return v,[x for x in v['attempts'] if now-x<60]
    def permitted(self,role):return len(self._state()[1])<3 if role=='call_start' else self.base.permitted(role)
    def reserve(self,role):
        if role!='call_start':return self.base.reserve(role)
        p=self.path.with_suffix('.lock')
        if p.is_symlink() or p.exists() and not plain_file(p.lstat()):raise ValueError('untrusted_call_counter_lock')
        with lock(p):
            v,t=self._state()
            if len(t)>=3:raise ValueError('call_rate_limited')
            v['call_start']+=1;v['attempts']=t+[self.clock()];atomic(self.path,v);self.observed=v['call_start'];return self.observed
