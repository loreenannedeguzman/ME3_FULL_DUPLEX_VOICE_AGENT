"""Ephemeral exact-scope Gmail read consent; no language/phrase classifier.

Only a completed host review and separately signed direct confirmation permit
one bounded read transaction. Unknown/restarted/uncertain operations never retry.
"""
import asyncio,copy,hashlib,hmac,json,secrets,threading,time
from .store import ReviewAuthority,StoreError,digest,_id,_hash,_generation,_stamp

READ_KEY_DOMAIN=b'ME3_GMAIL_READ_SCOPE_AUTHORITY_V1\x00'


class ReadGuardError(ValueError):pass


class ReadAuthority(ReviewAuthority):
    @classmethod
    def from_send_authority(cls,authority):
        if not isinstance(authority,ReviewAuthority):raise ReadGuardError('private_gmail_read_authority_required')
        # Separate purpose/key: a signed SEND proof cannot authorize any READ.
        key=hmac.new(authority._key,READ_KEY_DOMAIN,hashlib.sha256).digest()
        return cls(key,clock=authority.clock)


def binding(owner,goal,g,request):
    _id(owner);_id(goal);_generation(g)
    if not isinstance(request,str) or not request.strip() or len(request.encode())>4000 or any(ord(c)<32 and c not in '\n\t' for c in request):raise ReadGuardError('current_host_read_request_required')
    return dict(version=1,owner=owner,goal=goal,generation=g,request_sha256=hashlib.sha256(request.encode()).hexdigest(),source='direct_current_user')


def parameters_for(kind,parameters):
    fields={'recent':{'limit'},'search':{'subject','limit'},'read':{'selection_id','max_bytes'},'find_and_read':{'subject','max_bytes'}}
    if kind not in fields or not isinstance(parameters,dict) or set(parameters)-{'mailbox'}!=fields[kind]:raise ReadGuardError('exact_bounded_gmail_read_scope_required')
    p=copy.deepcopy(parameters)
    if 'mailbox' in p and p['mailbox'] not in ('INBOX','SENT'):raise ReadGuardError('configured_mailbox_scope_required')
    if 'limit' in p and (type(p['limit']) is not int or not 1<=p['limit']<=5):raise ReadGuardError('gmail_read_limit_1_to_5_required')
    if 'max_bytes' in p and (type(p['max_bytes']) is not int or not 1024<=p['max_bytes']<=32768):raise ReadGuardError('gmail_plain_read_byte_bound_required')
    if 'selection_id' in p:_id(p['selection_id'])
    if 'subject' in p and (not isinstance(p['subject'],str) or not 1<=len(p['subject'])<=200 or any(ord(c)<32 for c in p['subject'])):raise ReadGuardError('literal_gmail_subject_required')
    return p


class GuardedReads:
    def __init__(self,service,authority,clock=time.time):
        if authority is not None and not isinstance(authority,ReadAuthority):raise ReadGuardError('purpose_bound_gmail_read_authority_required')
        self.service=service;self.authority=authority;self.clock=clock;self.rows={};self.lock=threading.RLock()
    def prepare(self,op,owner,kind,parameters,host_goal,generation,current_user_request):
        _id(op);b=binding(owner,host_goal,generation,current_user_request);p=parameters_for(kind,parameters);mailbox=p.get('mailbox','INBOX');now=_stamp(self.clock())
        intent=dict(channel='gmail',command='read',kind=kind,parameters=p);expected=digest(intent);token=secrets.token_hex(32)
        scopes={
            'recent':lambda:'Read at most '+str(p['limit'])+' newest '+mailbox+' header summaries only; no message bodies.',
            'search':lambda:'Search '+mailbox+' for literal subject '+json.dumps(p['subject'],ensure_ascii=False)+' and return at most '+str(p['limit'])+' opaque matches; no headers or bodies.',
            'read':lambda:'Read only current selected '+mailbox+' message '+p['selection_id']+' as bounded plain text, at most '+str(p['max_bytes'])+' bytes; no attachments.',
            'find_and_read':lambda:'Search '+mailbox+' for literal subject '+json.dumps(p['subject'],ensure_ascii=False)+', then read only the newest matching ONE message as plain text, at most '+str(p['max_bytes'])+' bytes; no attachments. If no match, read no body.'}
        with self.lock:
            if op in self.rows:raise ReadGuardError('gmail_read_operation_exists_no_reprepare')
            if len(self.rows)>=32:raise ReadGuardError('gmail_read_session_operation_limit')
            for row in self.rows.values():
                if row['owner']==owner and row['state']=='PREPARED':row['state']='NOT_DISPATCHED'
            self.rows[op]=dict(owner=owner,token_hash=hashlib.sha256(token.encode()).hexdigest(),intent=intent,expected_digest=expected,created=now,expires=now+120,state='PREPARED',request_binding=b,result=None,result_sha256=None,confirmation_proof_sha256=None,mail_operations_attempted=0)
        return dict(operation_id=op,token=token,expected_digest=expected,intent=copy.deepcopy(intent),prompt='Allow this exact Gmail access? '+scopes[kind]()+' Confirm separately to proceed, or cancel.',expires_at=now+120,request_binding=copy.deepcopy(b))
    def _row(self,op,owner):
        _id(op);_id(owner);row=self.rows.get(op)
        if row is None:raise ReadGuardError('unknown_gmail_read_operation_no_retry')
        if row['owner']!=owner:raise ReadGuardError('gmail_read_owner_mismatch')
        if digest(row['intent'])!=row['expected_digest']:raise ReadGuardError('gmail_read_scope_changed')
        if row['result'] is not None and digest(row['result'])!=row['result_sha256']:raise ReadGuardError('gmail_read_cached_result_changed')
        return row
    def check(self,op,owner):
        with self.lock:
            row=self._row(op,owner)
            return copy.deepcopy(dict(operation_id=op,expected_digest=row['expected_digest'],intent=row['intent'],state=row['state'],result=row['result'],result_sha256=row['result_sha256'],confirmation_proof_sha256=row['confirmation_proof_sha256'],request_binding=row['request_binding'],verification_source='private_cached_gmail_read',read_only=True,network_dispatched=row['mail_operations_attempted']>0,mail_operations_attempted=row['mail_operations_attempted'],automatic_retry=False))
    def cancel(self,op,owner,expected_digest):
        with self.lock:
            row=self._row(op,owner)
            if row['expected_digest']!=expected_digest or row['state']!='PREPARED':raise ReadGuardError('unconsumed_exact_gmail_read_required')
            row['state']='NOT_DISPATCHED'
        return self.check(op,owner)
    async def commit(self,op,owner,token,expected_digest,proof,*,generation,host_goal,current_user_request):
        binding(owner,host_goal,generation,current_user_request);_hash(expected_digest)
        with self.lock:
            row=self._row(op,owner);now=_stamp(self.clock())
            if not isinstance(token,str) or len(token)!=64 or not hmac.compare_digest(row['token_hash'],hashlib.sha256(token.encode()).hexdigest()) or expected_digest!=row['expected_digest']:raise ReadGuardError('gmail_read_commit_binding_mismatch')
            if row['state']!='PREPARED':raise ReadGuardError('gmail_read_already_consumed_no_retry')
            if not row['created']<=now<row['expires']:raise ReadGuardError('gmail_read_scope_expired_or_clock_changed')
            if self.authority is None:raise ReadGuardError('signed_gmail_read_consent_required')
            self.authority.verify(proof,op,owner,expected_digest,generation,at=now)
            # This latch precedes any await, authentication or mailbox operation.
            row['state']='DISPATCHED';row['confirmation_proof_sha256']=digest(proof)
            kind=row['intent']['kind'];p=copy.deepcopy(row['intent']['parameters'])
        async def execute(method,*arguments):
            with self.lock:row['mail_operations_attempted']+=1
            value=await method(*arguments,owner,host_goal,generation,current_user_request,**({'mailbox':p['mailbox']} if p.get('mailbox','INBOX')!='INBOX' else {}))
            if p.get('mailbox','INBOX')=='SENT' and (not isinstance(value,dict) or value.get('mailbox')!='SENT'):raise ReadGuardError('confirmed_sent_folder_result_mismatch')
            return value
        try:
            if kind=='recent':result=await execute(self.service._recent,p['limit'])
            elif kind=='search':result=await execute(self.service._search,p['subject'],p['limit'])
            elif kind=='read':result=await execute(self.service._read,p['selection_id'],p['max_bytes'])
            else:
                search=await execute(self.service._search,p['subject'],1)
                matches=search.get('messages')
                if search.get('read_only') is not True or not isinstance(matches,list) or len(matches)>1:raise ReadGuardError('bounded_newest_search_unverified')
                if matches:
                    selected=_id(matches[0].get('selection_id'));message=await execute(self.service._read,selected,p['max_bytes'])
                    result=dict(search=search,message=message,no_content=False,read_only=True,content_is_untrusted=True,automatic_retry=False)
                else:result=dict(search=search,message=None,no_content=True,read_only=True,content_is_untrusted=True,automatic_retry=False)
            if not isinstance(result,dict) or result.get('read_only') is not True or len(json.dumps(result,ensure_ascii=False,allow_nan=False).encode())>100000:raise ReadGuardError('bounded_gmail_read_result_unverified')
            with self.lock:row['result']=copy.deepcopy(result);row['result_sha256']=digest(result);row['state']='VERIFIED'
        except BaseException as error:
            with self.lock:row['state']='UNCERTAIN';row['result']=None
            if isinstance(error,asyncio.CancelledError):raise
            raise ReadGuardError('gmail_read_failed_no_retry') from None
        return self.check(op,owner)
