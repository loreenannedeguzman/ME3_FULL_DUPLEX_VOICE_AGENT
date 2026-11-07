"""Bounded Pi-local, one-shot reminders. No model, sound, shell or network access."""
import hashlib,json,re,sqlite3,time
from contextlib import contextmanager,closing
from datetime import datetime,timezone
from pathlib import Path

UTC=timezone.utc
ID=re.compile('[0-9a-f]{32}')
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def instant(value):
    if not isinstance(value,str) or len(value)>40:raise ValueError('explicit_time_required')
    dt=datetime.fromisoformat(value.replace('Z','+00:00'))
    if dt.tzinfo is None or dt.utcoffset() is None:raise ValueError('timezone_offset_required')
    return dt.timestamp()
def iso(value):return datetime.fromtimestamp(value,UTC).isoformat()
def identifier(value):
    if not isinstance(value,str) or not ID.fullmatch(value):raise ValueError('invalid_reminder_id')
    return value

class Store:
    def __init__(self,path,clock=time.time):self.path=Path(path);self.clock=clock
    @contextmanager
    def connect(self):
        # Read/mutation entry points never create a missing store as a side effect.
        with closing(sqlite3.connect(self.path.resolve().as_uri()+'?mode=rw',uri=True,timeout=.2)) as c:
            c.row_factory=sqlite3.Row
            with c:yield c
    def initialize(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with closing(sqlite3.connect(self.path,timeout=.2)) as c:
            c.executescript('''CREATE TABLE IF NOT EXISTS reminders(id TEXT PRIMARY KEY,kind TEXT NOT NULL,label TEXT NOT NULL,due REAL NOT NULL,display_time TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN ('scheduled','due','acknowledged','canceled')),version INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY,digest TEXT NOT NULL,intent TEXT NOT NULL,target TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS health(id INTEGER PRIMARY KEY CHECK(id=1),heartbeat REAL NOT NULL);''')
    def active(self,c):
        r=c.execute('SELECT heartbeat FROM health WHERE id=1').fetchone()
        return bool(r and 0<=self.clock()-r[0]<=5)
    def tick(self):
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute('INSERT OR REPLACE INTO health VALUES(1,?)',(self.clock(),))
            c.execute("UPDATE reminders SET state='due',version=version+1 WHERE state='scheduled' AND due<=?",(self.clock(),))
    def shutdown(self):
        with self.connect() as c:c.execute('DELETE FROM health')
    def listing(self):
        if not self.path.exists():return {'available':False,'scheduler_active':False,'reminders':[],'clock_utc':iso(self.clock())}
        with self.connect() as c:
            rows=[dict(r) for r in c.execute("SELECT * FROM reminders WHERE state IN ('scheduled','due') ORDER BY due,id LIMIT 32")]
            return {'available':True,'scheduler_active':self.active(c),'clock_utc':iso(self.clock()),'reminders':rows,'delivery':'Due alerts remain visible in ME3 until dismissed. One fixed chime attempt uses an already active speaker-AEC voice session; idle, text and native-call modes do not open audio. Claimed or mixed PCM is not proof that sound was heard. GUI server must remain running; no wake-from-power-off, recurring alarm or spoken-label guarantee.'}
    def prepare(self,command,*,kind=None,label=None,due_at=None,delay_seconds=None,reminder_id=None):
        with self.connect() as c:
            if not self.active(c):raise ValueError('reminder_scheduler_not_running')
            if command=='schedule':
                if kind not in {'alarm','reminder'} or not isinstance(label,str) or not 1<=len(label.strip())<=120 or any(ord(ch)<32 for ch in label) or reminder_id is not None:raise ValueError('invalid_reminder')
                if (due_at is None)==(delay_seconds is None):raise ValueError('choose_explicit_time_or_delay')
                if delay_seconds is not None:
                    if type(delay_seconds) is not int or not 10<=delay_seconds<=2592000:raise ValueError('delay_must_be_10_seconds_to_30_days')
                    due=self.clock()+delay_seconds;display=iso(due)
                else:due=instant(due_at);display=due_at
                if not 5<=due-self.clock()<=2592000:raise ValueError('time_must_be_future_within_30_days')
                if c.execute("SELECT count(*) FROM reminders WHERE state IN ('scheduled','due')").fetchone()[0]>=32:raise ValueError('active_reminder_limit')
                return {'command':'schedule','kind':kind,'label':label.strip(),'due':due,'display_time':display}
            if command=='cancel':
                if any(v is not None for v in (kind,label,due_at,delay_seconds)):raise ValueError('cancel_requires_only_reminder_id')
                row=c.execute('SELECT * FROM reminders WHERE id=?',(identifier(reminder_id),)).fetchone()
                if row is None or row['state']!='scheduled':raise ValueError('reminder_not_pending_cannot_undo_due_alert')
                return {'command':'cancel','reminder_id':row['id'],'version':row['version'],'kind':row['kind'],'label':row['label'],'display_time':row['display_time']}
            raise ValueError('unsupported_reminder_command')
    def commit(self,operation_id,intent):
        identifier(operation_id);d=digest(intent)
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            old=c.execute('SELECT * FROM operations WHERE id=?',(operation_id,)).fetchone()
            if old:
                if old['digest']!=d:raise ValueError('operation_identity_changed')
                return {'operation_id':operation_id,'expected_digest':d,'target':old['target'],'duplicate':True}
            if not self.active(c):raise ValueError('reminder_scheduler_not_running')
            if c.execute('SELECT count(*) FROM operations').fetchone()[0]>=256:raise ValueError('reminder_history_limit')
            if intent['command']=='schedule':
                if intent['due']-self.clock()<5:raise ValueError('proposal_time_passed_make_fresh_request')
                if c.execute("SELECT count(*) FROM reminders WHERE state IN ('scheduled','due')").fetchone()[0]>=32:raise ValueError('active_reminder_limit')
                target=operation_id
                c.execute('INSERT INTO reminders VALUES(?,?,?,?,?,?,0)',(target,intent['kind'],intent['label'],intent['due'],intent['display_time'],'scheduled'))
            else:
                target=intent['reminder_id']
                # Due alert can no longer be canceled as if it had never happened.
                changed=c.execute("UPDATE reminders SET state='canceled',version=version+1 WHERE id=? AND state='scheduled' AND version=? AND due>?",(target,intent['version'],self.clock())).rowcount
                if changed!=1:raise ValueError('reminder_changed_or_already_due')
            c.execute('INSERT INTO operations VALUES(?,?,?,?)',(operation_id,d,json.dumps(intent,sort_keys=True),target))
            return {'operation_id':operation_id,'expected_digest':d,'target':target,'duplicate':False}
    def verify(self,operation_id):
        with self.connect() as c:
            op=c.execute('SELECT * FROM operations WHERE id=?',(identifier(operation_id),)).fetchone()
            if op is None:return {'row':None,'verification_source':'fresh_reminder_sqlite_connection'}
            row=c.execute('SELECT * FROM reminders WHERE id=?',(op['target'],)).fetchone()
            return {'operation_id':operation_id,'expected_digest':op['digest'],'intent':json.loads(op['intent']),'row':dict(row),'scheduler_active':self.active(c),'verification_source':'fresh_reminder_sqlite_connection'}
    def acknowledge(self,reminder_id):
        with self.connect() as c:
            return c.execute("UPDATE reminders SET state='acknowledged',version=version+1 WHERE id=? AND state='due'",(identifier(reminder_id),)).rowcount==1
