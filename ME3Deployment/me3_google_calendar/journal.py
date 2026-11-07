"""Private immutable proposals; durable claim BEFORE any write HTTP attempt.

Confirmation authority is the existing host private MCP lifecycle. These methods
must never be registered as public read/model tools. No credentials or HMAC key.
"""
import hashlib,hmac,json,math,os,re,secrets,sqlite3,stat,time
from contextlib import contextmanager,closing
from pathlib import Path
from .domain import need,opaque,digest,canonical,ACCOUNT,CALENDAR

STATES={'PREPARED','SUPERSEDED','DISPATCHED','UNCERTAIN','REJECTED_ETAG','WRITE_ACKNOWLEDGED','VERIFIED'}

class Journal:
    def __init__(self,root,path=None,clock=time.time):
        self.root=Path(root).absolute();self.path=Path(path or self.root/'data/google-calendar-operations.sqlite3').absolute();self.clock=clock
        need(self.root==self.root.resolve() and self.path.resolve().is_relative_to(self.root),'private_calendar_journal_required')
    def _safe(self,missing=False):
        need(self.path.resolve().is_relative_to(self.root) and not any(p.is_symlink() for p in (self.path,*self.path.parents)),'direct_calendar_journal_required')
        if self.path.exists():
            s=self.path.stat();need(stat.S_ISREG(s.st_mode) and s.st_nlink==1 and s.st_size<=32*1024*1024,'bounded_calendar_journal_required')
            if os.name!='nt':need(s.st_uid==os.getuid() and stat.S_IMODE(s.st_mode)==0o600,'owned_private_calendar_journal_required')
        else:need(missing,'calendar_journal_not_initialized')
    def initialize(self):
        self._safe(True);self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        fd=os.open(self.path,os.O_RDWR|os.O_CREAT|getattr(os,'O_NOFOLLOW',0),0o600);os.close(fd)
        with self.connect() as c:
            c.executescript('''CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY,owner TEXT NOT NULL,token_hash TEXT NOT NULL,intent TEXT NOT NULL,digest TEXT NOT NULL,state TEXT NOT NULL,created REAL NOT NULL,expires REAL NOT NULL,updated REAL NOT NULL,receipt TEXT);
            CREATE TABLE IF NOT EXISTS events(sequence INTEGER PRIMARY KEY AUTOINCREMENT,operation_id TEXT NOT NULL,state TEXT NOT NULL,at REAL NOT NULL);''')
    @contextmanager
    def connect(self,read=False):
        self._safe()
        with closing(sqlite3.connect(self.path.as_uri()+('?mode=ro' if read else '?mode=rw'),uri=True,timeout=2)) as c:
            c.row_factory=sqlite3.Row
            if not read:c.execute('PRAGMA synchronous=FULL')
            with c:yield c
    def _now(self):
        at=self.clock();need(type(at) in {int,float} and math.isfinite(at) and at>=0,'calendar_clock_invalid');return at
    def _row(self,c,operation_id,owner):
        row=c.execute('SELECT * FROM operations WHERE id=?',(opaque(operation_id),)).fetchone();need(row is not None and row['owner']==opaque(owner),'unknown_or_foreign_calendar_operation')
        intent=json.loads(row['intent']);need(digest(intent)==row['digest'] and intent.get('channel')=='google_calendar' and intent.get('account')==ACCOUNT and intent.get('calendar')==CALENDAR and row['state'] in STATES,'immutable_calendar_operation_corrupted')
        return row,intent
    def _state(self,c,oid,state,now,receipt=None):
        c.execute('UPDATE operations SET state=?,updated=?,receipt=? WHERE id=?',(state,now,canonical(receipt) if receipt is not None else None,oid));c.execute('INSERT INTO events(operation_id,state,at) VALUES(?,?,?)',(oid,state,now))
    def prepare(self,operation_id,owner,intent):
        opaque(operation_id);opaque(owner);now=self._now();expected=digest(intent);token=secrets.token_hex(32)
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE');need(not c.execute('SELECT 1 FROM operations WHERE id=?',(operation_id,)).fetchone(),'calendar_operation_exists_no_reprepare')
            for row in c.execute("SELECT id FROM operations WHERE owner=? AND state='PREPARED'",(owner,)).fetchall():self._state(c,row['id'],'SUPERSEDED',now)
            c.execute('INSERT INTO operations VALUES(?,?,?,?,?,?,?,?,?,?)',(operation_id,owner,hashlib.sha256(token.encode()).hexdigest(),canonical(intent),expected,'PREPARED',now,now+120,now,None));c.execute('INSERT INTO events(operation_id,state,at) VALUES(?,?,?)',(operation_id,'PREPARED',now))
        return dict(operation_id=operation_id,token=token,expected_digest=expected,intent=intent,expires_in_seconds=120)
    def claim(self,operation_id,owner,token,expected_digest,confirmation):
        need(confirmation=='confirm' and isinstance(token,str) and re.fullmatch('[0-9a-f]{64}',token) and isinstance(expected_digest,str) and re.fullmatch('[0-9a-f]{64}',expected_digest),'separate_exact_calendar_confirmation_required')
        now=self._now()
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE');row,intent=self._row(c,operation_id,owner)
            need(row['state']=='PREPARED' and row['created']<=now<row['expires'],'calendar_operation_consumed_expired_or_superseded_no_retry')
            need(hmac.compare_digest(row['token_hash'],hashlib.sha256(token.encode()).hexdigest()) and hmac.compare_digest(row['digest'],expected_digest),'calendar_confirmation_binding_mismatch')
            self._state(c,operation_id,'DISPATCHED',now)
        return intent
    def outcome(self,operation_id,owner,expected_digest,state,receipt=None):
        need(state in {'UNCERTAIN','REJECTED_ETAG','WRITE_ACKNOWLEDGED','VERIFIED'},'calendar_outcome_invalid')
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE');row,intent=self._row(c,operation_id,owner)
            need(row['digest']==expected_digest and row['state'] in {'DISPATCHED','UNCERTAIN','WRITE_ACKNOWLEDGED','VERIFIED'},'calendar_outcome_state_invalid')
            if state!='VERIFIED':need(row['state']=='DISPATCHED','calendar_outcome_already_recorded')
            self._state(c,operation_id,state,self._now(),receipt)
    def read_operation(self,operation_id,owner):
        with self.connect(True) as c:
            row,intent=self._row(c,operation_id,owner)
            return dict(operation_id=operation_id,expected_digest=row['digest'],intent=intent,state=row['state'],receipt=json.loads(row['receipt']) if row['receipt'] else None,verification_source='local_google_calendar_journal_only',read_only=True)
