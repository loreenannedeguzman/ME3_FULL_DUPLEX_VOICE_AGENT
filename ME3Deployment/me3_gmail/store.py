"""Private durable Gmail operations; signed review, once-only DATA claim.

Stores approved mail content, not account/AppPassword credentials. Local SQLite
proof is explicitly distinct from SMTP submission or a fresh Gmail Sent read.
The host owns current-command/source-policy validation and the signing key.
"""
import hashlib,hmac,json,math,os,re,secrets,sqlite3,stat,time
from contextlib import contextmanager,closing
from pathlib import Path
from .transport import Draft,_wire,_plain_identity

CONFIRMATION='direct_current_user_after_completed_prompt'
DOMAIN=b'ME3_GMAIL_EXACT_REVIEW_CONFIRMATION_V1\x00'
MAX_DB=32*1024*1024
STATES={'PREPARED','DISPATCHED','SMTP_ACCEPTED','SENT_COPY_OBSERVED','UNCERTAIN','REJECTED','NOT_DISPATCHED'}


class StoreError(ValueError):pass


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
def digest(value):return hashlib.sha256(canonical(value).encode()).hexdigest()
def _id(value):
    if not isinstance(value,str) or re.fullmatch('[0-9a-f]{32}',value) is None:raise StoreError('opaque_gmail_identity_required')
    return value
def _hash(value):
    if not isinstance(value,str) or re.fullmatch('[0-9a-f]{64}',value) is None:raise StoreError('gmail_digest_required')
    return value
def _generation(value):
    if type(value) is not int or not 1<=value<=2147483647:raise StoreError('current_host_generation_required')
    return value
def _stamp(value):
    if type(value) not in {int,float} or not math.isfinite(value) or value<0:raise StoreError('valid_gmail_clock_required')
    return value


class ReviewAuthority:
    """Host-only signer/verifier. Never register sign as an MCP/tool endpoint.

    Key bytes are provided by the host's private authority, not Gmail account
    credentials. A distinct HMAC domain prevents another action's proof reuse.
    """
    def __init__(self,key,clock=time.time):
        if not isinstance(key,bytes) or not 32<=len(key)<=128:raise StoreError('private_host_signing_key_required')
        self._key=key;self.clock=clock
    def sign(self,operation_id,owner,expected_digest,generation):
        now=_stamp(self.clock())
        value=dict(version=1,operation_id=_id(operation_id),owner=_id(owner),expected_digest=_hash(expected_digest),generation=_generation(generation),source=CONFIRMATION,issued_at=now,expires_at=now+60,nonce=secrets.token_hex(16))
        return {**value,'mac':hmac.new(self._key,DOMAIN+canonical(value).encode(),hashlib.sha256).hexdigest()}
    def verify(self,proof,operation_id,owner,expected_digest,generation,*,at=None):
        fields={'version','operation_id','owner','expected_digest','generation','source','issued_at','expires_at','nonce','mac'}
        if not isinstance(proof,dict) or set(proof)!=fields:raise StoreError('signed_exact_gmail_review_required')
        value={k:v for k,v in proof.items() if k!='mac'}
        if type(value['version']) is not int or value['version']!=1 or value['source']!=CONFIRMATION:raise StoreError('signed_exact_gmail_review_required')
        _id(value['nonce']);_generation(value['generation'])
        if (value['operation_id'],value['owner'],value['expected_digest'],value['generation'])!=(operation_id,owner,expected_digest,generation):raise StoreError('gmail_confirmation_binding_mismatch')
        start=_stamp(value['issued_at']);end=_stamp(value['expires_at']);now=_stamp(self.clock() if at is None else at)
        if not start<=now<end or not 0<end-start<=60:raise StoreError('gmail_confirmation_expired_or_clock_changed')
        expected=hmac.new(self._key,DOMAIN+canonical(value).encode(),hashlib.sha256).hexdigest()
        if not isinstance(proof['mac'],str) or not hmac.compare_digest(proof['mac'],expected):raise StoreError('signed_exact_gmail_review_required')
        return True


class Store:
    def __init__(self,root,path=None,*,authority=None,clock=time.time):
        self.root=Path(root).absolute();self.path=Path(path or self.root/'data/gmail-operations.sqlite3').absolute();self.authority=authority;self.clock=clock
        if self.root!=self.root.resolve() or not self.path.resolve().is_relative_to(self.root):raise StoreError('project_local_gmail_store_required')
    def _safe(self,missing=False):
        if not self.path.resolve().is_relative_to(self.root) or any(p.is_symlink() for p in [self.path,*self.path.parents]):raise StoreError('direct_private_gmail_store_required')
        if self.path.exists():
            s=self.path.stat()
            if not stat.S_ISREG(s.st_mode) or s.st_nlink!=1 or s.st_size>MAX_DB:raise StoreError('bounded_single_gmail_store_required')
            if os.name!='nt' and (s.st_uid!=os.getuid() or stat.S_IMODE(s.st_mode)!=0o600):raise StoreError('private_owned_gmail_store_required')
        elif not missing:raise StoreError('gmail_store_not_initialized')
    def initialize(self):
        self._safe(True);self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        handle=os.open(self.path,os.O_RDWR|os.O_CREAT|getattr(os,'O_NOFOLLOW',0),0o600);os.close(handle);self._safe()
        with self.connect() as c:
            c.executescript('''CREATE TABLE IF NOT EXISTS operations(
            id TEXT PRIMARY KEY,owner TEXT NOT NULL,token_hash TEXT NOT NULL,
            intent TEXT NOT NULL,digest TEXT NOT NULL,wire BLOB NOT NULL,
            state TEXT NOT NULL,created REAL NOT NULL,expires REAL NOT NULL,updated REAL NOT NULL,
            claim_generation INTEGER,claimed_at REAL,claim_proof TEXT,receipt TEXT);
            CREATE TABLE IF NOT EXISTS events(sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            operation_id TEXT NOT NULL,state TEXT NOT NULL,at REAL NOT NULL);''')
    @contextmanager
    def connect(self,read_only=False):
        self._safe()
        with closing(sqlite3.connect(self.path.resolve().as_uri()+('?mode=ro' if read_only else '?mode=rw'),uri=True,timeout=2)) as c:
            c.row_factory=sqlite3.Row
            if not read_only:c.execute('PRAGMA synchronous=FULL')
            with c:yield c
    def _now(self):return _stamp(self.clock())
    def _row(self,c,operation_id,owner):
        row=c.execute('SELECT * FROM operations WHERE id=?',(_id(operation_id),)).fetchone()
        if row is None:raise StoreError('unknown_gmail_operation')
        if row['owner']!=_id(owner):raise StoreError('gmail_operation_owner_mismatch')
        intent=json.loads(row['intent']);wire=bytes(row['wire'])
        fields={'channel','command','sender','recipient','subject','body','message_id','wire_sha256'}
        if (set(intent)!=fields or intent['channel']!='gmail' or intent['command']!='send' or digest(intent)!=row['digest']
            or hashlib.sha256(wire).hexdigest()!=intent['wire_sha256'] or row['state'] not in STATES):raise StoreError('gmail_immutable_operation_corrupted')
        try:expected=_wire(intent['sender'],intent['recipient'],intent['subject'],intent['body'],intent['message_id'])
        except Exception:raise StoreError('gmail_immutable_operation_corrupted') from None
        if expected!=wire:raise StoreError('gmail_immutable_operation_corrupted')
        if row['claim_proof'] is not None:
            if self.authority is None or row['claimed_at'] is None:raise StoreError('gmail_claim_authority_unavailable')
            if row['state'] in {'PREPARED','NOT_DISPATCHED'}:raise StoreError('gmail_claim_state_corrupted')
            self.authority.verify(json.loads(row['claim_proof']),operation_id,owner,row['digest'],row['claim_generation'],at=row['claimed_at'])
        elif row['state'] not in {'PREPARED','NOT_DISPATCHED'}:raise StoreError('gmail_dispatch_proof_missing')
        if row['state'] in {'SMTP_ACCEPTED','SENT_COPY_OBSERVED'}:
            receipt=json.loads(row['receipt']) if row['receipt'] else None
            if self._receipt(row['state'],receipt,intent,wire)!=receipt:raise StoreError('gmail_outcome_receipt_corrupted')
        elif row['receipt'] is not None:raise StoreError('gmail_outcome_receipt_corrupted')
        return row,intent
    @staticmethod
    def _draft(row,intent):
        return Draft(intent['sender'],intent['recipient'],intent['subject'],intent['body'],intent['message_id'],bytes(row['wire']),intent['wire_sha256'])
    @staticmethod
    def _event(c,operation_id,state,now):c.execute('INSERT INTO events(operation_id,state,at) VALUES(?,?,?)',(operation_id,state,now))
    def prepare(self,operation_id,owner,value,*,ttl=180):
        _id(operation_id);_id(owner)
        if type(ttl) is not int or not 30<=ttl<=300 or not isinstance(value,Draft):raise StoreError('bounded_immutable_gmail_draft_required')
        try:wire=_wire(value.sender,value.recipient,value.subject,value.body,value.message_id)
        except Exception:raise StoreError('bounded_immutable_gmail_draft_required') from None
        if wire!=value.wire or hashlib.sha256(wire).hexdigest()!=value.sha256:raise StoreError('bounded_immutable_gmail_draft_required')
        intent=dict(channel='gmail',command='send',sender=value.sender,recipient=value.recipient,subject=value.subject,body=value.body,message_id=value.message_id,wire_sha256=value.sha256)
        token=secrets.token_hex(32);expected=digest(intent);now=self._now()
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute('SELECT 1 FROM operations WHERE id=?',(operation_id,)).fetchone():raise StoreError('gmail_operation_exists_no_reprepare')
            stale=c.execute("SELECT id FROM operations WHERE owner=? AND state='PREPARED'",(owner,)).fetchall()
            for old in stale:
                c.execute("UPDATE operations SET state='NOT_DISPATCHED',updated=? WHERE id=? AND state='PREPARED'",(now,old['id']));self._event(c,old['id'],'NOT_DISPATCHED',now)
            c.execute('INSERT INTO operations(id,owner,token_hash,intent,digest,wire,state,created,expires,updated) VALUES(?,?,?,?,?,?,?,?,?,?)',(operation_id,owner,hashlib.sha256(token.encode()).hexdigest(),canonical(intent),expected,wire,'PREPARED',now,now+ttl,now));self._event(c,operation_id,'PREPARED',now)
        prompt='Send this Gmail message?\nFrom: '+value.sender+'\nTo: '+value.recipient+'\nSubject: '+value.subject+'\n\n'+value.body+'\n\nPlease confirm or cancel.'
        return dict(operation_id=operation_id,token=token,expected_digest=expected,intent=intent,prompt=prompt,expires_at=now+ttl)
    def draft_for(self,operation_id,owner):
        with self.connect(True) as c:
            row,intent=self._row(c,operation_id,owner);return self._draft(row,intent)
    def cancel(self,operation_id,owner,expected_digest):
        return self.outcome(operation_id,owner,expected_digest,'NOT_DISPATCHED')
    def claim(self,operation_id,owner,token,expected_digest,proof,*,generation):
        _hash(expected_digest);_generation(generation)
        if self.authority is None:raise StoreError('signed_gmail_review_authority_required')
        if not isinstance(token,str) or re.fullmatch('[0-9a-f]{64}',token) is None:raise StoreError('private_gmail_commit_token_required')
        now=self._now()
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE');row,intent=self._row(c,operation_id,owner)
            if not hmac.compare_digest(row['token_hash'],hashlib.sha256(token.encode()).hexdigest()) or row['digest']!=expected_digest:raise StoreError('gmail_commit_binding_mismatch')
            if row['state']!='PREPARED':raise StoreError('gmail_operation_already_claimed_no_replay')
            if not row['created']<=now<row['expires']:raise StoreError('gmail_review_expired_or_clock_changed')
            self.authority.verify(proof,operation_id,owner,expected_digest,generation,at=now)
            changed=c.execute("UPDATE operations SET state='DISPATCHED',updated=?,claim_generation=?,claimed_at=?,claim_proof=? WHERE id=? AND state='PREPARED'",(now,generation,now,canonical(proof),operation_id)).rowcount
            if changed!=1:raise StoreError('gmail_operation_already_claimed_no_replay')
            self._event(c,operation_id,'DISPATCHED',now)
            return self._draft(row,intent)
    @staticmethod
    def _receipt(status,receipt,intent,wire):
            if status=='SMTP_ACCEPTED':
                if not isinstance(receipt,dict) or receipt.get('status')!='SMTP_ACCEPTED' or receipt.get('smtp_code')!=250 or receipt.get('draft_sha256')!=intent['wire_sha256'] or receipt.get('delivery_verified') is not False or receipt.get('read_verified') is not False or receipt.get('automatic_retry') is not False:raise StoreError('actual_smtp_acceptance_receipt_required')
                safe={k:receipt[k] for k in ('status','smtp_code','draft_sha256','delivery_verified','read_verified','automatic_retry')}
            elif status=='SENT_COPY_OBSERVED':
                if (not isinstance(receipt,dict) or receipt.get('status')!=status or receipt.get('draft_sha256')!=intent['wire_sha256'] or receipt.get('message_id')!=intent['message_id'] or receipt.get('sent_copy_verified') is not True or receipt.get('submission_verified') is not True or receipt.get('delivery_verified') is not False or receipt.get('read_verified') is not False or receipt.get('read_only') is not True or receipt.get('automatic_retry') is not False or receipt.get('mailbox_role')!='sent' or receipt.get('verification_source')!='fresh_gmail_sent_exact_message_id'):raise StoreError('fresh_exact_sent_copy_receipt_required')
                for name in ('uid','uidvalidity'):
                    if not isinstance(receipt.get(name),str) or re.fullmatch('[1-9][0-9]{0,19}',receipt[name]) is None:raise StoreError('fresh_exact_sent_copy_receipt_required')
                for name in ('raw_sha256','content_sha256'):_hash(receipt.get(name))
                if receipt['content_sha256']!=digest(_plain_identity(wire)):raise StoreError('sent_copy_content_binding_mismatch')
                if type(receipt.get('message_bytes')) is not int or not 1<=receipt['message_bytes']<=65536:raise StoreError('bounded_sent_receipt_required')
                safe={k:receipt[k] for k in ('status','draft_sha256','message_id','sent_copy_verified','submission_verified','delivery_verified','read_verified','read_only','automatic_retry','mailbox_role','uid','uidvalidity','raw_sha256','content_sha256','message_bytes','verification_source')}
            elif status in {'UNCERTAIN','REJECTED','NOT_DISPATCHED'}:
                if receipt is not None:raise StoreError('raw_error_content_must_not_be_stored')
                safe=None
            else:raise StoreError('unsupported_gmail_outcome')
            return safe
    def outcome(self,operation_id,owner,expected_digest,status,receipt=None):
        _hash(expected_digest);now=self._now()
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE');row,intent=self._row(c,operation_id,owner)
            if row['digest']!=expected_digest:raise StoreError('gmail_outcome_binding_mismatch')
            safe=self._receipt(status,receipt,intent,bytes(row['wire']))
            if row['state']==status:
                if row['receipt']!=(canonical(safe) if safe is not None else None):raise StoreError('gmail_outcome_receipt_changed')
                return self._proof(row,intent)
            allowed={'PREPARED':{'NOT_DISPATCHED'},'DISPATCHED':{'SMTP_ACCEPTED','UNCERTAIN','REJECTED','SENT_COPY_OBSERVED'},'SMTP_ACCEPTED':{'SENT_COPY_OBSERVED'},'UNCERTAIN':{'SENT_COPY_OBSERVED'}}
            if status not in allowed.get(row['state'],set()):raise StoreError('gmail_outcome_transition_forbidden')
            if now<row['updated']:raise StoreError('gmail_clock_reversed')
            c.execute('UPDATE operations SET state=?,receipt=?,updated=? WHERE id=?',(status,canonical(safe) if safe is not None else None,now,operation_id));self._event(c,operation_id,status,now)
        return self.read_operation(operation_id,owner)
    @staticmethod
    def _proof(row,intent):
        state=row['state'];receipt=json.loads(row['receipt']) if row['receipt'] else None
        return dict(operation_id=row['id'],expected_digest=row['digest'],intent=intent,wire_sha256=intent['wire_sha256'],state=state,claimed=state not in {'PREPARED','NOT_DISPATCHED'},confirmation_proof_sha256=hashlib.sha256(row['claim_proof'].encode()).hexdigest() if row['claim_proof'] else None,smtp_accepted=state in {'SMTP_ACCEPTED','SENT_COPY_OBSERVED'},sent_copy_verified=state=='SENT_COPY_OBSERVED',delivery_verified=False,read_verified=False,receipt=receipt,verification_source='fresh_gmail_operation_sqlite_connection',local_operation_proof_only=True,automatic_retry=False,replayed=False)
    def read_operation(self,operation_id,owner):
        with self.connect(True) as c:
            row,intent=self._row(c,operation_id,owner);return self._proof(row,intent)
