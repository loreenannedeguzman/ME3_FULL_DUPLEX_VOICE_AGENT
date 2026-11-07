"""Observed output-token admission. Persistent forecasts, never a quota oracle or call refund."""
import json,math,sqlite3,time,uuid
from contextlib import contextmanager,closing
from pathlib import Path
from me3_assistant.admission import AdmissionDenied
from onit_candidate.reasoning_profile import GROQ_MODEL as MODEL
class OutputQuota:
    def __init__(self,path=None,*,limit=None,clock=time.time):
        self.path=Path(path) if path is not None else None;self.clock=clock;self.limit=limit;self.memory=[]
        if limit is not None and (type(limit) is not int or not 1<=limit<=1_000_000_000):raise ValueError('invalid_output_limit')
    @classmethod
    def project(cls,root):
        folder=Path(root)/'evidence/assistant/provider-admission';policy=folder/'onit-output-policy.json'
        if not policy.exists():return cls()
        if policy.is_symlink() or policy.stat().st_size>16384:raise ValueError('invalid_output_policy')
        data=json.loads(policy.read_bytes())
        if data.get('version')!=1 or data.get('model')!=MODEL or data.get('scope')!='output_tokens_per_minute':raise ValueError('invalid_output_policy')
        return cls(folder/'onit-output.sqlite3',limit=data['limit'])
    def cap(self,requested):
        if type(requested) is not int or not 1<=requested<=2048:raise ValueError('invalid_output_request')
        return min(requested,max(1,min(512,self.limit//2))) if self.limit is not None else requested
    def now(self):
        n=self.clock()
        if type(n) not in (int,float) or not math.isfinite(n) or n<0:raise AdmissionDenied('invalid_admission_state')
        return n
    @contextmanager
    def connection(self):
        if self.path.is_symlink() or self.path.parent.is_symlink():raise AdmissionDenied('invalid_admission_state')
        self.path.parent.mkdir(parents=True,exist_ok=True)
        c=sqlite3.connect(self.path,timeout=3);c.execute('CREATE TABLE IF NOT EXISTS reservations (ticket TEXT PRIMARY KEY, started REAL NOT NULL, ended REAL, declared INTEGER NOT NULL, actual INTEGER, dispatched INTEGER NOT NULL DEFAULT 0)');c.execute('CREATE INDEX IF NOT EXISTS output_time ON reservations(ended,started)');c.commit()
        try:
            yield c;c.commit()
        except BaseException:c.rollback();raise
        finally:c.close()
    def rows(self,c,now):
        if c is None:
            rows=list(self.memory)
        else:rows=[dict(zip(['ticket','started','ended','declared','actual','dispatched'],r)) for r in c.execute('SELECT ticket,started,ended,declared,actual,dispatched FROM reservations WHERE COALESCE(ended,started)>? ORDER BY COALESCE(ended,started)',(now-60,))]
        return [r for r in rows if (r['ended'] if r['ended'] is not None else r['started'])>now-60]
    def delay_for(self,rows,cap,now):
        if self.limit is None:return 0
        if cap>self.limit:raise AdmissionDenied('estimated_request_too_large')
        spent=sum(r['actual'] if r['actual'] is not None else r['declared'] for r in rows)
        if spent+cap<=self.limit:return 0
        for row in sorted(rows,key=lambda r:r['ended'] if r['ended'] is not None else r['started']):
            spent-=row['actual'] if row['actual'] is not None else row['declared']
            if spent+cap<=self.limit:return max(0,(row['ended'] if row['ended'] is not None else row['started'])+60-now)
        raise AdmissionDenied('invalid_admission_state')
    def delay(self,cap):
        now=self.now()
        if self.path is None:return self.delay_for(self.rows(None,now),cap,now)
        if not self.path.exists():return 0
        if self.path.is_symlink():raise AdmissionDenied('invalid_admission_state')
        with closing(sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True,timeout=3)) as c:
            latest=c.execute('SELECT MAX(COALESCE(ended,started)) FROM reservations').fetchone()[0]
            if latest is not None and latest>now:raise AdmissionDenied('clock_reversed')
            return self.delay_for(self.rows(c,now),cap,now)
    def reserve(self,cap):
        now=self.now();ticket=uuid.uuid4().hex
        if self.path is None:
            if self.delay_for(self.rows(None,now),cap,now):raise AdmissionDenied('known_token_balance')
            self.memory.append(dict(ticket=ticket,started=now,ended=None,declared=cap,actual=None,dispatched=0));return ticket
        with self.connection() as c:
            c.execute('BEGIN IMMEDIATE')
            latest=c.execute('SELECT MAX(COALESCE(ended,started)) FROM reservations').fetchone()[0]
            if latest is not None and latest>now:raise AdmissionDenied('clock_reversed')
            if self.delay_for(self.rows(c,now),cap,now):raise AdmissionDenied('known_token_balance')
            c.execute('INSERT INTO reservations(ticket,started,declared) VALUES(?,?,?)',(ticket,now,cap));return ticket
    def finish(self,ticket,actual,dispatched):
        now=self.now()
        if type(dispatched) is not bool or actual is not None and (type(actual) is not int or actual<0):raise ValueError('invalid_output_observation')
        if self.path is None:
            row=next(r for r in self.memory if r['ticket']==ticket)
            if row['ended'] is not None:raise ValueError('output_ticket_already_settled')
            row.update(ended=now,actual=actual if dispatched else 0,dispatched=int(dispatched));return
        with self.connection() as c:
            c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT started,ended FROM reservations WHERE ticket=?',(ticket,)).fetchone()
            if row is None or row[1] is not None or row[0]>now:raise ValueError('invalid_output_ticket')
            c.execute('UPDATE reservations SET ended=?,actual=?,dispatched=? WHERE ticket=?',(now,actual if dispatched else 0,int(dispatched),ticket))

class CombinedAdmission:
    """Keep existing RPD/TPM authority and add only the observed output dimension."""
    def __init__(self,base,output,emit):self.base=base;self.output=output;self.emit=emit;self.cap=None;self.ticket=None;self.actual=None;self.dispatched=False
    def __getattr__(self,name):return getattr(self.base,name)
    def capacity_delay(self,role,tokens,maximum_seconds):
        generic=self.base.capacity_delay(role,tokens,maximum_seconds)
        delay=self.output.delay(self.cap) if role=='reasoning' and self.cap is not None else 0
        if delay>maximum_seconds:return generic
        if delay:self.emit('onit_output_capacity_wait',role=role,seconds=delay,limit=self.output.limit,declared_output=self.cap,provider_capacity_verified=False,wire_attempts=0)
        return max(generic,delay)
    def admit(self,role,tokens):
        self.actual=None;self.dispatched=False
        self.ticket=self.output.reserve(self.cap) if role=='reasoning' and self.cap is not None else None
        try:return self.base.admit(role,tokens)
        except BaseException:
            if self.ticket:self.output.finish(self.ticket,0,False);self.ticket=None
            raise
    def finish(self,role,ticket):
        try:self.base.finish(role,ticket)
        finally:
            if self.ticket:self.output.finish(self.ticket,self.actual,self.dispatched);self.ticket=None
    def observe_event(self,event,fields):
        if event=='groq_request_dispatch' and fields.get('role')=='reasoning':self.dispatched=True
        if event=='groq_reported_token_usage' and fields.get('role')=='reasoning' and type(fields.get('completion_tokens')) is int:self.actual=fields['completion_tokens']
