"""GUI-owned durable one-shot delivery attempts, separate from reminder ack."""
import math,re,sqlite3,uuid
from contextlib import closing,contextmanager
from pathlib import Path

class Delivery:
    def __init__(self,path,store,console):
        self.path=Path(path);self.store=store;self.console=console;self.lanes=[]
        if self.path.is_symlink() or not self.path.resolve().is_relative_to(store.path.resolve().parent):raise ValueError('private_reminder_delivery_store_required')
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with closing(sqlite3.connect(self.path,timeout=.2)) as c:
            c.execute("CREATE TABLE IF NOT EXISTS delivery(reminder_id TEXT PRIMARY KEY,claim_id TEXT UNIQUE NOT NULL,owner TEXT NOT NULL,due REAL NOT NULL,state TEXT NOT NULL CHECK(state IN ('CLAIMED','PCM_MIXED','INTERRUPTED','HISTORICAL_NO_REPLAY')),mixed_bytes INTEGER NOT NULL DEFAULT 0)")
            c.execute('CREATE TABLE IF NOT EXISTS activation(id INTEGER PRIMARY KEY CHECK(id=1),cutoff REAL NOT NULL,historical_count INTEGER NOT NULL)')
            c.commit()
    @contextmanager
    def db(self):
        with closing(sqlite3.connect(self.path,timeout=.2)) as c:
            c.row_factory=sqlite3.Row
            with c:yield c
    def bind(self,lane):self.lanes.append(lane)
    def baseline_existing_due(self):
        """Explicit first-deployment step only; never called by GUI startup.

        Existing past-due reminders remain visible and unacknowledged. They do
        not become new sound attempts merely because delivery was installed.
        A durable marker also covers an initially empty store, so later genuine
        reminders cannot be reclassified by repeating source activation.
        """
        cutoff=self.store.clock()
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE');old=c.execute('SELECT * FROM activation WHERE id=1').fetchone()
            if old:return dict(initialized=False,historical_count=old['historical_count'],state='HISTORICAL_NO_REPLAY')
            with self.store.connect() as source:
                rows=source.execute("SELECT id,due FROM reminders WHERE state IN ('scheduled','due') AND due<=?",(cutoff,)).fetchall()
            count=0
            for row in rows:
                count+=c.execute("INSERT OR IGNORE INTO delivery(reminder_id,claim_id,owner,due,state) VALUES(?,?,?,?,'HISTORICAL_NO_REPLAY')",(row['id'],uuid.uuid4().hex,'source_activation',row['due'])).rowcount
            c.execute('INSERT INTO activation VALUES(1,?,?)',(cutoff,count))
            return dict(initialized=True,historical_count=count,state='HISTORICAL_NO_REPLAY')
    def drain(self):
        for lane in list(self.lanes):
            for report in lane.drain():
                if report['state'] not in {'PCM_MIXED','INTERRUPTED'}:raise ValueError('invalid_reminder_audio_outcome')
                with self.db() as c:
                    c.execute("UPDATE delivery SET state=?,mixed_bytes=? WHERE reminder_id=? AND claim_id=? AND state='CLAIMED'",(report['state'],report['mixed_bytes'],report['reminder_id'],report['claim_id']))
            if lane.closed:self.lanes.remove(lane)
    def cancel(self,reminder_id):
        for lane in self.lanes:lane.cancel(reminder_id)
        self.drain()
    def tick(self):
        self.store.tick();self.drain()
        rows=self.store.listing()['reminders'];due={r['id']:r for r in rows if r['state']=='due'}
        for lane in self.lanes:
            with lane.lock:queued=[i['reminder_id'] for i in lane.queue]
            for reminder_id in queued:
                if reminder_id not in due:lane.cancel(reminder_id)
        lane=getattr(self.console,'reminder_lane',None)
        if (lane is None or self.console.calls.held or self.console.mode!='voice'
                or not self.console.voice_armed or not lane.available()):return
        for row in due.values():
            if not lane.available():break
            if not re.fullmatch('[a-f0-9]{32}',row['id']) or not math.isfinite(row['due']):raise ValueError('invalid_due_reminder')
            claim=uuid.uuid4().hex
            # At-most-once ATTEMPT survives process loss. A claim is not sound
            # delivery or dismissal. Unknown/partial attempts are never replayed.
            with self.db() as c:
                c.execute('BEGIN IMMEDIATE')
                changed=c.execute("INSERT OR IGNORE INTO delivery(reminder_id,claim_id,owner,due,state) VALUES(?,?,?,?,'CLAIMED')",(row['id'],claim,self.console.shared.conversation_id,row['due'])).rowcount
            if changed and not lane.enqueue(row['id'],claim):
                with self.db() as c:c.execute("UPDATE delivery SET state='INTERRUPTED' WHERE reminder_id=? AND claim_id=? AND state='CLAIMED'",(row['id'],claim))
        self.drain()
