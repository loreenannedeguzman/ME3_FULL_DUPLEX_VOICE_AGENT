"""Bind normal-mode authorization to config/source and the unchanged shared caps."""
import asyncio,errno,hashlib,json,math,os,sys,time
from contextlib import contextmanager
from datetime import datetime,timezone
from pathlib import Path
from benchmark_m6.storage import atomic,read
from speech_mcp.client import ProviderLimited
from task_mcp.client import ROOT

PHASE=ROOT/'evidence/completion/autonomous-20261020-001'
NORMAL=ROOT/'evidence/assistant'
KEYS={'asr','reasoning','tts','commit_item'}

@contextmanager
def lock(path,*,timeout=0):
    if type(timeout) not in {int,float} or not math.isfinite(timeout) or not 0<=timeout<=2:raise ValueError('invalid_resource_lease_timeout')
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    f=path.open('a+b')
    try:
        if f.seek(0,2)==0:f.write(b'0');f.flush()
        f.seek(0)
        deadline=time.monotonic()+timeout
        while True:
            try:
                if os.name=='nt':
                    import msvcrt
                    msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
                else:
                    import fcntl
                    fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
                break
            except OSError as exc:
                remaining=deadline-time.monotonic()
                if timeout==0 or remaining<=0 or exc.errno not in {errno.EACCES,errno.EAGAIN}:raise ValueError('assistant_resource_lease_busy') from None
                time.sleep(min(.01,remaining))
        try:yield
        finally:
            f.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(f,fcntl.LOCK_UN)
    finally:f.close()

def legacy_running():
    if sys.platform!='linux':return False
    for path in Path('/proc').glob('[0-9]*/cmdline'):
        try:args=path.read_bytes().decode(errors='replace').split('\0')
        except (FileNotFoundError,PermissionError):continue
        if '-m' in args and args.index('-m')+1<len(args):
            module=args[args.index('-m')+1]
            if module in {'me3_agent.runner','benchmark_agent.runner','benchmark_final.runner','benchmark_m6.runner'}:return True
    return False

def verify_sources(root=ROOT,source_path=None):
    root=Path(root);source=read(source_path or root/'evidence/assistant/candidate-source.json')
    if any(hashlib.sha256((root/n).read_bytes()).hexdigest()!=h for n,h in source['source_hashes'].items()):raise ValueError('normal_assistant_source_changed')
    return source

def authorize(config,source,grant_path=None,phase=PHASE,now=None,mode='normal',backend=None):
    if source.get('operating_profile')=='standing':
        from me3_assistant.operating import authorize_operating
        return authorize_operating(config,source,mode,backend)
    phase=Path(phase);grant=read(grant_path or NORMAL/'authorization.json');now=now or datetime.now(timezone.utc)
    auth_path=phase/'authorization.json';authority=read(auth_path);usage=read(phase/'budget.json')
    from me3_assistant.backends import selected
    selected(grant,backend)
    from me3_assistant.acoustic_echo import audio_mode
    audio_mode(grant) # Explicit project grant; default earphones, no environment override.
    if mode not in {'normal','text'}:raise ValueError('unsupported_assistant_mode')
    if grant.get('approved') is not True or grant.get('mode')!=mode or grant.get('provider_allowance_confirmed') is not True or grant.get('operator_ready') is not True:raise ValueError('normal_live_authorization_required')
    if grant.get('source_revision')!=source['revision'] or grant.get('config_sha256')!=config['sha256'] or grant.get('budget_authorization_sha256')!=hashlib.sha256(auth_path.read_bytes()).hexdigest():raise ValueError('normal_authorization_binding_mismatch')
    for entry in config['approved_modules']:
        name=entry['module'].replace('.','/')+'.py'
        if source['source_hashes'].get(name)!=entry['sha256']:raise ValueError('server_module_not_in_approved_source')
    if authority.get('approved') is not True or authority.get('limits')!=source['budget_limits'] or grant.get('runtime')!=config['runtime']:raise ValueError('normal_authorization_limits_mismatch')
    issued=datetime.fromisoformat(grant['issued_at_utc']);expires=datetime.fromisoformat(grant['expires_at_utc'])
    if issued.tzinfo is None or expires.tzinfo is None or not 0<=(now-issued).total_seconds()<=900 or not issued<expires or (expires-issued).total_seconds()>3600 or now>=expires:raise ValueError('fresh_normal_allowance_readiness_required')
    start=grant.get('budget_start',{});extra=grant.get('max_reservations',{})
    if set(start)!=KEYS or set(extra)!=KEYS or set(source['budget_limits'])!=KEYS or set(usage)!=KEYS:raise ValueError('normal_budget_keys_required')
    for key,limit in source['budget_limits'].items():
        if any(type(v) is not int for v in [start[key],extra[key],usage[key],limit]) or not 0<=start[key]<=usage[key]<=limit or not 0<=extra[key]<=limit-start[key]:raise ValueError('normal_budget_invalid')
    if mode=='normal':
        if extra['asr']-(usage['asr']-start['asr'])<1 or extra['reasoning']-(usage['reasoning']-start['reasoning'])<1 or extra['tts']-(usage['tts']-start['tts'])<4:raise ValueError('insufficient_startup_and_answer_allowance')
    elif any(extra[k]!=0 for k in ('asr','tts','commit_item')) or grant.get('allowed_actions')!=[] or extra['reasoning']-(usage['reasoning']-start['reasoning'])<1:
        raise ValueError('text_requires_readonly_reasoning_only_allowance')
    actions={a['name'] for a in config['actions']}
    if 'workspace_access' in grant and type(grant['workspace_access']) is not bool:raise ValueError('invalid_workspace_authorization')
    if grant.get('workspace_access') is True and not any(s.get('workspace_tools') for s in config['servers']):raise ValueError('workspace_configuration_required')
    if not isinstance(grant.get('allowed_actions'),list) or not set(grant['allowed_actions'])<=actions:raise ValueError('invalid_action_authorization')
    from me3_assistant.local_actions import validate_local_actions,LocalActionBudget
    validate_local_actions(grant,config)
    for role,n in grant.get('local_action_limits',{}).items():
        if n:LocalActionBudget(SharedBudget(grant,authority['limits'],phase),grant,NORMAL/'local-action-budgets')._used(role)
    if legacy_running():raise ValueError('legacy_voice_session_active')
    return grant,authority['limits']

class SharedBudget:
    def __new__(cls,grant,limits,phase=PHASE):
        if grant.get('profile')=='standing':
            from me3_assistant.operating import OperatingBudget
            return OperatingBudget(grant,limits,phase)
        return super().__new__(cls)
    def __init__(self,grant,limits,phase=PHASE):
        self.grant=grant;self.limits=limits;self.phase=Path(phase);self.exhausted=asyncio.Event()
    def permitted(self,role):
        data=read(self.phase/'budget.json')
        if hashlib.sha256((self.phase/'authorization.json').read_bytes()).hexdigest()!=self.grant['budget_authorization_sha256']:raise ValueError('budget_authority_changed')
        if set(data)!=KEYS or any(type(data[k]) is not int or not self.grant['budget_start'][k]<=data[k]<=self.limits[k] for k in KEYS):raise ValueError('shared_budget_invalid_or_decreased')
        return role in self.limits and data[role]<self.limits[role] and data[role]-self.grant['budget_start'][role]<self.grant['max_reservations'][role]
    def remaining(self,role):
        if not self.permitted(role):return 0
        data=read(self.phase/'budget.json')
        return max(0,min(self.limits[role]-data[role],self.grant['max_reservations'][role]-(data[role]-self.grant['budget_start'][role])))
    def reserve(self,role):
        with lock(self.phase/'normal-reservation.lock'):
            if legacy_running():raise ValueError('legacy_voice_session_active')
            expires=datetime.fromisoformat(self.grant['expires_at_utc'])
            if datetime.now(timezone.utc)>=expires:raise ValueError('normal_authorization_expired')
            data=read(self.phase/'budget.json')
            if not self.permitted(role):
                self.exhausted.set();raise ProviderLimited('local_authorized_budget_exhausted')
            if role in {'asr','reasoning'}:
                path=self.phase/'rate.json';now=time.monotonic();rate=[x for x in (read(path) if path.exists() else []) if 0<=now-x<60]
                if len(rate)>=14:
                    self.exhausted.set();raise ProviderLimited('unchanged_local_rate_guard')
            data[role]+=1;atomic(self.phase/'budget.json',data)
            if role in {'asr','reasoning'}:atomic(path,rate+[now])
            return data[role]
