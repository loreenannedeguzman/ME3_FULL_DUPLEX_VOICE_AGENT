"""Private append-only subscription-request audit, not a quota or billing system.

SharedBudget remains the sole request-reservation authority. An ordinal is only
recorded here: it is never allocated, decremented, borrowed, reset or refunded.
DISPATCHED is the host's dispatch fence, not proof of remote acceptance/billing.
None usage means unknown; no token allowance or output cap is invented.
"""
import hashlib,json,math,os,re,secrets,stat,threading,time
from contextlib import contextmanager
from pathlib import Path

STATES={'RESERVED','DISPATCHED','COMPLETED','FAILED','INTERRUPTED'}
TERMINAL={'COMPLETED','FAILED','INTERRUPTED'}
REASONS={'transport_error','provider_error','response_failed','response_incomplete','cancelled','process_interrupted','dispatch_failed','source_changed','validation_failed','worker_stopped','unknown',*[f'http_{n}' for n in (400,401,403,408,429,500,502,503,504)]}
MAX_BYTES=32*1024*1024
MAX_EVENTS=100000
ZERO='0'*64
class UsageError(ValueError):pass
def need(value,code):
    if not value:raise UsageError(code)
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')
def digest(value):return hashlib.sha256(canonical(value)).hexdigest()
def unique(pairs):
    out={}
    for key,value in pairs:need(key not in out,'usage_duplicate_json_key');out[key]=value
    return out
def stamp(value):
    need(type(value) in {int,float} and math.isfinite(value) and value>=0,'usage_clock_invalid');return value
def tokens(value):
    if value is None:return None
    need(isinstance(value,dict),'usage_tokens_invalid')
    result={name:value.get(name) for name in ('input_tokens','output_tokens','total_tokens')}
    need(all(v is None or type(v) is int and 0<=v<=10**10 for v in result.values()),'usage_tokens_invalid')
    if all(v is None for v in result.values()):return None
    if all(type(v) is int for v in result.values()):need(result['total_tokens']==result['input_tokens']+result['output_tokens'],'usage_tokens_inconsistent')
    if 'hosted_web_search' in value:
        web=value['hosted_web_search']
        need(type(web) is dict and set(web)=={'observed_calls','num_requests'},'usage_hosted_search_invalid')
        observed=web['observed_calls'];reported=web['num_requests']
        need(type(observed) is int and 1<=observed<=64 and (reported is None or type(reported) is int and 1<=reported<=1024),'usage_hosted_search_invalid')
        result['hosted_web_search']=dict(observed_calls=observed,num_requests=reported)
    return result

def text(value,pattern,code):
    need(isinstance(value,str) and re.fullmatch(pattern,value) is not None,code);return value

def parse(raw):
    """Replay and validate the complete immutable prefix, never repair/truncate."""
    need(isinstance(raw,bytes) and len(raw)<=MAX_BYTES,'usage_journal_size')
    need(not raw or raw.endswith(b'\n'),'usage_torn_journal')
    events=[];latest={};ordinals=set();previous=ZERO
    for line in raw.splitlines():
        need(len(events)<MAX_EVENTS and 1<=len(line)<=4096,'usage_event_bound')
        try:row=json.loads(line,object_pairs_hook=unique,parse_constant=lambda _:(_ for _ in ()).throw(UsageError('usage_nonfinite_JSON')))
        except (ValueError,UnicodeError):raise UsageError('usage_journal_invalid') from None
        need(isinstance(row,dict) and set(row)=={'version','sequence','previous_sha256','event_sha256','ticket','state','at','ordinal','model','session_id','account_sha256','usage','response_id','reason_code'},'usage_event_fields')
        supplied=row['event_sha256'];body={k:v for k,v in row.items() if k!='event_sha256'}
        need(row['version']==1 and type(row['version']) is int and type(row['sequence']) is int and row['sequence']==len(events)+1 and row['previous_sha256']==previous and isinstance(supplied,str) and supplied==digest(body),'usage_chain_invalid')
        text(row['ticket'],r'[a-f0-9]{32}','usage_ticket_invalid');need(row['state'] in STATES,'usage_state_invalid');stamp(row['at'])
        need(type(row['ordinal']) is int and row['ordinal']>0,'usage_ordinal_invalid')
        text(row['model'],r'[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}','usage_model_invalid')
        text(row['session_id'],r'[A-Za-z0-9_.:-]{0,128}','usage_session_invalid')
        if row['account_sha256'] is not None:text(row['account_sha256'],r'[a-f0-9]{64}','usage_account_reference_invalid')
        need(row['usage']==tokens(row['usage']),'usage_tokens_invalid')
        if row['response_id'] is not None:text(row['response_id'],r'resp_[A-Za-z0-9_-]{1,155}','usage_response_ID_invalid')
        old=latest.get(row['ticket'])
        if old is None:
            need(row['state']=='RESERVED' and row['ordinal'] not in ordinals and row['usage'] is None and row['response_id'] is None and row['reason_code'] is None,'usage_reservation_invalid');ordinals.add(row['ordinal'])
        else:
            need(all(row[k]==old[k] for k in ('ordinal','model','session_id','account_sha256')),'usage_ticket_binding_changed')
            need(old['state'] not in TERMINAL,'usage_terminal_repeated')
            need(row['state'] in ({'DISPATCHED','FAILED','INTERRUPTED'} if old['state']=='RESERVED' else TERMINAL),'usage_transition_invalid')
            if row['state']=='DISPATCHED':need(row['usage'] is None and row['response_id'] is None and row['reason_code'] is None,'usage_dispatch_invalid')
            elif row['state']=='COMPLETED':need(old['state']=='DISPATCHED' and row['reason_code'] is None,'usage_completion_before_dispatch')
            else:
                need(row['reason_code'] in REASONS,'usage_reason_invalid')
                if old['state']=='RESERVED':need(row['usage'] is None and row['response_id'] is None,'usage_undispatched_usage_invalid')
        previous=supplied;latest[row['ticket']]=row;events.append(row)
    return events,latest

class UsageLedger:
    """Existing data/openai-plan must already be an owned0700 directory."""
    def __init__(self,data_root,clock=time.time):
        self.directory=Path(data_root).absolute()/'openai-plan';self.clock=clock;self._thread=threading.Lock()
        need('..' not in self.directory.parts,'usage_path_invalid')
        need(os.name=='posix' and os.open in os.supports_dir_fd,'usage_private_storage_requires_POSIX')
    def _info(self,value,directory=False):
        need((stat.S_ISDIR(value.st_mode) if directory else stat.S_ISREG(value.st_mode) and value.st_nlink==1) and value.st_uid==os.geteuid() and value.st_mode&0o077==0,'usage_storage_permissions_or_links')
    @contextmanager
    def _locked(self):
        need(self._thread.acquire(blocking=False),'usage_ledger_busy');fd=None;lockfd=None
        try:
            fd=os.open(self.directory.anchor,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            for index,part in enumerate(self.directory.parts[1:]):
                nxt=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);os.close(fd);fd=nxt;info=os.fstat(fd)
                if index==len(self.directory.parts)-2:self._info(info,True)
                else:need(info.st_uid in {0,os.geteuid()} and info.st_mode&0o022==0,'usage_untrusted_storage_ancestor')
            lockfd=os.open('subscription-usage.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600,dir_fd=fd);self._info(os.fstat(lockfd))
            import fcntl
            try:fcntl.flock(lockfd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise UsageError('usage_ledger_busy') from None
            yield fd
        except OSError:raise UsageError('usage_storage_unavailable_or_link') from None
        finally:
            if lockfd is not None:os.close(lockfd)
            if fd is not None:os.close(fd)
            self._thread.release()
    def _raw(self,fd):
        try:handle=os.open('subscription-usage.jsonl',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
        except FileNotFoundError:return b''
        try:
            before=os.fstat(handle);self._info(before);need(before.st_size<=MAX_BYTES,'usage_journal_size')
            with os.fdopen(handle,'rb',closefd=False) as stream:raw=stream.read(MAX_BYTES+1)
            after=os.fstat(handle)
            need((before.st_size,before.st_mtime_ns)==(after.st_size,after.st_mtime_ns) and len(raw)==after.st_size,'usage_journal_changed')
            return raw
        finally:os.close(handle)
    def _append(self,fd,line):
        handle=os.open('subscription-usage.jsonl',os.O_CREAT|os.O_WRONLY|os.O_APPEND|os.O_NOFOLLOW,0o600,dir_fd=fd)
        try:
            info=os.fstat(handle);self._info(info);need(info.st_size+len(line)<=MAX_BYTES,'usage_journal_size')
            need(os.write(handle,line)==len(line),'usage_append_incomplete_no_retry');os.fsync(handle);os.fsync(fd)
        finally:os.close(handle)
    def _event(self,fd,events,body):
        row=dict(version=1,sequence=len(events)+1,previous_sha256=events[-1]['event_sha256'] if events else ZERO,**body)
        row['event_sha256']=digest(row);line=canonical(row)+b'\n'
        # Validate exactly the proposed next transition before appending bytes.
        parse(b''.join(canonical(e)+b'\n' for e in events)+line)
        self._append(fd,line);return dict(row)
    def reserve(self,ordinal,model,session_id='',account_sha256=None):
        with self._locked() as fd:
            events,_=parse(self._raw(fd));ticket=secrets.token_hex(16)
            self._event(fd,events,dict(ticket=ticket,state='RESERVED',at=stamp(self.clock()),ordinal=ordinal,model=model,session_id=session_id,account_sha256=account_sha256,usage=None,response_id=None,reason_code=None));return ticket
    def _transition(self,ticket,state,usage=None,response_id=None,reason_code=None):
        text(ticket,r'[a-f0-9]{32}','usage_ticket_invalid')
        with self._locked() as fd:
            events,latest=parse(self._raw(fd));need(ticket in latest,'usage_ticket_unknown');old=latest[ticket]
            body={k:old[k] for k in ('ticket','ordinal','model','session_id','account_sha256')}
            body.update(state=state,at=stamp(self.clock()),usage=tokens(usage),response_id=response_id,reason_code=reason_code)
            return self._event(fd,events,body)
    def dispatch(self,ticket):return self._transition(ticket,'DISPATCHED')
    def complete(self,ticket,usage=None,response_id=None):return self._transition(ticket,'COMPLETED',usage,response_id)
    def fail(self,ticket,reason_code='provider_error',usage=None,response_id=None):return self._transition(ticket,'FAILED',usage,response_id,reason_code)
    def interrupt(self,ticket,reason_code='process_interrupted',usage=None,response_id=None):return self._transition(ticket,'INTERRUPTED',usage,response_id,reason_code)
    def events(self):
        with self._locked() as fd:return parse(self._raw(fd))[0]
    def snapshot(self):
        with self._locked() as fd:
            events,latest=parse(self._raw(fd))
            return {'requests_reserved':len(latest),'events':len(events),'tickets':list(latest.values()),'last_event_sha256':events[-1]['event_sha256'] if events else ZERO,'token_allowance_inferred':False,'request_refunds':0}
