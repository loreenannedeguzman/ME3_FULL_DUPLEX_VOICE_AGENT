"""Separate session-local action counters; never alters hosted/Piper/shopping ledgers."""
import hashlib,json
from datetime import datetime,timezone
from pathlib import Path
from benchmark_m6.storage import read,atomic
from me3_assistant.guard import lock
ROLES={'music_control','reminder_control'}

def validate_local_actions(grant,config):
    caps=grant.get('local_action_limits',{})
    if not isinstance(caps,dict) or set(caps)-ROLES or any(type(v) is not int or not 0<=v<=16 for v in caps.values()):raise ValueError('invalid_local_action_allowance')
    for role,n in caps.items():
        if n and not any(a['adapter']==role and a['name'] in grant['allowed_actions'] for a in config['actions']):raise ValueError('local_action_not_authorized')

def grant_binding(grant):return hashlib.sha256(json.dumps(grant,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def initialize_local_budget(grant,directory):
    """Authorization-time operation only; exclusive creation cannot reset a grant."""
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True);binding=grant_binding(grant);path=directory/(binding+'.json')
    with path.open('x',encoding='utf-8') as f:json.dump({'grant_sha256':binding,'music_control':0,**{r:0 for r in grant.get('local_action_limits',{})}},f)
    return path

class LocalActionBudget:
    def __init__(self,base,grant,directory):
        self.base=base;self.grant=grant;self.caps={'music_control':0,**grant.get('local_action_limits',{})};self.binding=grant_binding(grant);self.path=Path(directory)/(self.binding+'.json');self.observed={r:0 for r in self.caps}
        if set(self.caps)-ROLES or any(type(n) is not int or not 0<=n<=16 for n in self.caps.values()):raise ValueError('invalid_local_action_allowance')
    def _used(self,role='music_control'):
        if not self.caps.get(role,0):return 0
        if datetime.now(timezone.utc)>=datetime.fromisoformat(self.grant['expires_at_utc']):raise ValueError('local_action_grant_expired')
        self.base.permitted('asr') # Validate existing shared authority/counter monotonicity; no reservation.
        if not self.path.exists():raise ValueError('local_action_budget_not_initialized')
        data=read(self.path)
        if set(data)!={'grant_sha256',*self.caps} or data['grant_sha256']!=self.binding or any(type(data[r]) is not int or not self.observed[r]<=data[r]<=n for r,n in self.caps.items()):raise ValueError('local_action_counter_invalid')
        self.observed={r:data[r] for r in self.caps}
        return data[role]
    def permitted(self,role):return self._used(role)<self.caps.get(role,0) if role in ROLES else self.base.permitted(role)
    def reserve(self,role):
        if role not in ROLES:return self.base.reserve(role)
        with lock(self.path.with_suffix('.lock')):
            if not self.permitted(role):raise ValueError('local_action_allowance_exhausted')
            used=self._used(role)+1;data=read(self.path);data[role]=used;atomic(self.path,data);self.observed[role]=used;return used
