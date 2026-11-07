"""Observed Groq quota admission, not an account quota oracle or tokenizer.

Request headers are RPD; token headers are TPM. Unknown balances stay unknown.
Only metadata is persisted. Project reservations remain owned by SharedBudget.
"""
import copy,json,math,re,time,uuid
from contextlib import contextmanager
from pathlib import Path

ROLES={'asr','reasoning'}
REASONS={'provider_cooldown','known_request_balance','known_token_balance','estimated_request_too_large','model_request_active','invalid_admission_state','clock_reversed','invalid_request_size','invalid_token_budget','capacity_wait_budget'}
class AdmissionDenied(ValueError):
    code=429
    def __init__(self,reason):
        assert reason in REASONS
        self.reason=reason;super().__init__('provider_admission_denied')

def duration(value):
    if not isinstance(value,str) or len(value)>64:return None
    if re.fullmatch(r'\d+(?:\.\d+)?',value):total=float(value)
    else:
        parts=list(re.finditer(r'(\d+(?:\.\d+)?)(ms|s|m|h|d)',value))
        if not parts or ''.join(p[0] for p in parts)!=value:return None
        total=sum(float(p[1])*{'ms':.001,'s':1,'m':60,'h':3600,'d':86400}[p[2]] for p in parts)
    return total if math.isfinite(total) and 0<=total<=86400 else None

def estimated_tokens(role,payload):
    if role=='asr':return 0
    obj=payload.get('json',{});output=obj.get('max_completion_tokens',1024) if isinstance(obj,dict) else None
    if type(output) is not int or not 1<=output<=65536:raise AdmissionDenied('invalid_token_budget')
    try:raw=json.dumps(obj,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')
    except (TypeError,ValueError,UnicodeError,RecursionError):raise AdmissionDenied('invalid_request_size') from None
    if len(raw)>524288:raise AdmissionDenied('invalid_request_size')
    # Planning estimate only. Includes declared output/thinking capacity and a
    # framing margin; UTF-8 bytes/3 is NOT a certified model token upper bound.
    size=len(raw)
    # Count text after HTTP JSON decoding, not escaped transport serialization.
    # This is still an inexact bytes/3 forecast, not a tokenizer or quota oracle.
    allowed={'model','messages','response_format','max_completion_tokens','reasoning_effort','temperature','stream','tool_choice','include_reasoning'}
    messages=obj.get('messages') if isinstance(obj,dict) else None
    fmt=obj.get('response_format') if isinstance(obj,dict) else None
    if (isinstance(obj,dict) and set(obj)<=allowed and isinstance(fmt,dict)
        and fmt.get('type') in {'json_object','json_schema'} and isinstance(messages,list)
        and all(isinstance(m,dict) and set(m)=={'role','content'} and m['role'] in {'system','user','assistant'} and isinstance(m['content'],str) for m in messages)):
        size=sum(len(m['content'].encode('utf-8'))+len(m['role'])+16 for m in messages)
        size+=len(json.dumps(fmt,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8'))
    return (size+2)//3+128+output

def blank():
    return dict(request_limit=None,remaining_requests=None,request_reset=0,token_limit=None,remaining_tokens=None,token_reset=0,blocked_until=0,lease=None)

class Admission:
    def __init__(self,path=None,*,clock=time.time):
        self.path=Path(path) if path is not None else None;self.clock=clock;self.memory=None
    def valid(self,state):
        def num(v):return type(v) in {int,float} and math.isfinite(v) and 0<=v<=1e12
        assert set(state)=={'version','updated_at','roles'} and type(state['version']) is int and state['version']==1
        assert num(state['updated_at']) and isinstance(state['roles'],dict) and set(state['roles'])<=ROLES
        for n in state['roles'].values():
            assert isinstance(n,dict) and set(n)==set(blank())
            for k in ('request_limit','remaining_requests','token_limit','remaining_tokens'):
                assert n[k] is None or type(n[k]) is int and 0<=n[k]<=1_000_000_000
            for k in ('request_reset','token_reset','blocked_until'):assert num(n[k])
            for a,b in [('remaining_requests','request_limit'),('remaining_tokens','token_limit')]:
                assert n[a] is None or n[b] is None or n[a]<=n[b]
            if n['lease'] is not None:
                lease=n['lease'];assert set(lease)=={'id','expires'} and re.fullmatch('[0-9a-f]{32}',lease['id']) and num(lease['expires'])
    @contextmanager
    def transaction(self,*,readonly=False):
        from contextlib import nullcontext
        from me3_assistant.guard import lock
        from benchmark_m6.storage import atomic
        now=self.clock()
        if type(now) not in {int,float} or not math.isfinite(now) or not 0<=now<=1e12:raise AdmissionDenied('invalid_admission_state')
        if self.path is not None:
            if self.path.is_symlink() or self.path.parent.is_symlink() or self.path.with_suffix('.lock').is_symlink():raise AdmissionDenied('invalid_admission_state')
            self.path.parent.mkdir(parents=True,exist_ok=True)
        with lock(self.path.with_suffix('.lock')) if self.path is not None else nullcontext():
            try:
                if self.path is not None and self.path.exists():
                    if self.path.stat().st_size>16384:raise ValueError()
                    state=json.loads(self.path.read_bytes())
                else:state=copy.deepcopy(self.memory) if self.memory is not None else {'version':1,'updated_at':now,'roles':{}}
                self.valid(state)
            except (ValueError,TypeError,KeyError,AssertionError,AttributeError,OSError,RecursionError):raise AdmissionDenied('invalid_admission_state') from None
            if now<state['updated_at']:raise AdmissionDenied('clock_reversed')
            yield state,now
            if readonly:return
            state['updated_at']=now;self.valid(state)
            if self.path is not None:atomic(self.path,state)
            else:self.memory=state
    def capacity_delay(self,role,tokens,maximum_seconds):
        """Inspect one bounded TPM-reset wait before admission; never reserve or refill.

        Daily limits, active requests, cooldowns and impossible payloads still
        fail at admission. This is not a retry after a failed wire attempt.
        """
        if role not in ROLES or type(tokens) is not int or tokens<0:raise AdmissionDenied('invalid_token_budget')
        if type(maximum_seconds) not in (int,float) or not 0<=maximum_seconds<=60:raise ValueError('invalid_capacity_wait')
        if role!='reasoning' or not maximum_seconds:return 0
        with self.transaction(readonly=True) as (state,now):
            n=state['roles'].get(role,blank())
            if n['blocked_until']>now or n['lease'] and n['lease']['expires']>now:return 0
            if n['request_limit']==0 or n['request_reset']>now and n['remaining_requests'] is not None and n['remaining_requests']<1:return 0
            if n['token_limit'] is not None and tokens>n['token_limit']:return 0
            delay=n['token_reset']-now
            if n['remaining_tokens'] is not None and tokens>n['remaining_tokens'] and 0<delay<=maximum_seconds:return delay
            return 0
    def admit(self,role,tokens):
        if role not in ROLES or type(tokens) is not int or tokens<0:raise AdmissionDenied('invalid_token_budget')
        with self.transaction() as (state,now):
            n=state['roles'].setdefault(role,blank())
            if n['blocked_until']>now:raise AdmissionDenied('provider_cooldown')
            if n['lease'] and n['lease']['expires']>now:raise AdmissionDenied('model_request_active')
            # Expiry permits the next explicit user request to ask the server.
            # It does not invent a refilled balance or dispatch any request.
            for balance,reset in [('remaining_requests','request_reset'),('remaining_tokens','token_reset')]:
                if n[reset]<=now:n[balance]=None
            if n['request_limit']==0 or n['remaining_requests'] is not None and n['remaining_requests']<1:raise AdmissionDenied('known_request_balance')
            if role=='reasoning':
                if n['token_limit'] is not None and tokens>n['token_limit']:raise AdmissionDenied('estimated_request_too_large')
                if n['remaining_tokens'] is not None and tokens>n['remaining_tokens']:raise AdmissionDenied('known_token_balance')
            ticket=uuid.uuid4().hex;n['lease']={'id':ticket,'expires':now+60}
            if n['remaining_requests'] is not None:n['remaining_requests']-=1
            if role=='reasoning' and n['remaining_tokens'] is not None:n['remaining_tokens']-=tokens
            return ticket
    def observe(self,role,ticket,headers,status):
        with self.transaction() as (state,now):
            n=state['roles'].get(role)
            if not n or not n['lease'] or n['lease']['id']!=ticket:return
            allowed={'retry-after','x-ratelimit-limit-requests','x-ratelimit-remaining-requests','x-ratelimit-reset-requests','x-ratelimit-limit-tokens','x-ratelimit-remaining-tokens','x-ratelimit-reset-tokens'}
            h={k.lower():v for k,v in headers.items() if isinstance(k,str) and k.lower() in allowed}
            for name,prefix,reset,fallback in [('requests','request','request_reset',86400),('tokens','token','token_reset',60)]:
                if role=='asr' and name=='tokens':continue
                def integer(key):
                    v=h.get(key);return int(v) if isinstance(v,str) and re.fullmatch('[0-9]{1,10}',v) and int(v)<=1_000_000_000 else None
                limit=integer('x-ratelimit-limit-'+name);remaining=integer('x-ratelimit-remaining-'+name)
                effective_limit=limit if limit is not None else n[prefix+'_limit']
                if remaining is not None and (effective_limit is None or remaining<=effective_limit):
                    # Server observation replaces only this local forecast;
                    # project call reservations are never decremented/refunded.
                    n['remaining_'+name]=remaining
                    delta=duration(h.get('x-ratelimit-reset-'+name));n[reset]=now+(fallback if delta is None else delta)
                if limit is not None:
                    n[prefix+'_limit']=limit
                    if n['remaining_'+name] is not None:n['remaining_'+name]=min(n['remaining_'+name],limit)
            if status==429:
                wait=duration(h.get('retry-after'));n['blocked_until']=max(n['blocked_until'],now+max(1,60 if wait is None else wait))
            elif status in {500,502,503,504}:
                # Recent unavailability gates a future explicit request only.
                # No retry, quota refill or healthy-provider proof is implied.
                wait=duration(h.get('retry-after'));n['blocked_until']=max(n['blocked_until'],now+min(300,max(1,30 if wait is None else wait)))
    def finish(self,role,ticket):
        with self.transaction() as (state,now):
            n=state['roles'].get(role)
            if n and n['lease'] and n['lease']['id']==ticket:n['lease']=None

def project_admission():
    from task_mcp.client import ROOT
    return Admission(ROOT/'evidence/assistant/provider-admission/groq.json')
