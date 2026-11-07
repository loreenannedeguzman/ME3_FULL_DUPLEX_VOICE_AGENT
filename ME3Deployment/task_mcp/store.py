"""Bounded real Pi shopping-list state. No provider or speech dependency."""
import hashlib,json,re,sqlite3,unicodedata
from contextlib import closing
from datetime import datetime,timezone
from pathlib import Path

def item_value(value):
    if not isinstance(value,str):raise ValueError('invalid_item')
    value=' '.join(unicodedata.normalize('NFKC',value).lower().split())
    if not re.fullmatch(r"[a-z][a-z0-9 '-]{0,59}",value):raise ValueError('invalid_item')
    return value

def quantity_value(value):
    if type(value) is not int or not 1<=value<=20:raise ValueError('invalid_quantity')
    return value

def operation_value(value):
    if not isinstance(value,str) or not re.fullmatch(r'[a-f0-9]{32}',value):raise ValueError('invalid_operation')
    return value

def state_digest(row):
    return hashlib.sha256(json.dumps(row,sort_keys=True,separators=(',',':')).encode()).hexdigest()

class ShoppingStore:
    def __init__(self,path,write_budget=1):
        self.path=Path(path).resolve();self.write_budget=write_budget;self.writes=0
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with closing(sqlite3.connect(self.path,timeout=.2)) as db:
            db.execute('CREATE TABLE IF NOT EXISTS items(operation_id TEXT PRIMARY KEY,item TEXT NOT NULL UNIQUE,quantity INTEGER NOT NULL CHECK(quantity BETWEEN 1 AND 20),created_utc TEXT NOT NULL)')
        if self.path.is_file():self.path.chmod(0o600)
    def add(self,item,quantity,operation_id):
        item=item_value(item);quantity=quantity_value(quantity);operation_id=operation_value(operation_id)
        with closing(sqlite3.connect(self.path,timeout=.2,isolation_level=None)) as db:
            db.row_factory=sqlite3.Row;db.execute('PRAGMA synchronous=FULL');db.execute('BEGIN IMMEDIATE')
            try:
                row=db.execute('SELECT * FROM items WHERE operation_id=?',(operation_id,)).fetchone()
                if row:
                    if row['item']!=item or row['quantity']!=quantity:raise ValueError('operation_conflict')
                    db.rollback();return {'duplicate':True,'operation_id':operation_id}
                if self.writes>=self.write_budget:raise ValueError('task_write_budget')
                if db.execute('SELECT 1 FROM items WHERE item=?',(item,)).fetchone():raise ValueError('item_already_exists')
                if db.execute('SELECT COUNT(*) FROM items').fetchone()[0]>=100:raise ValueError('list_capacity')
                db.execute('INSERT INTO items VALUES(?,?,?,?)',(operation_id,item,quantity,datetime.now(timezone.utc).isoformat()))
                db.commit();self.writes+=1
                return {'duplicate':False,'operation_id':operation_id}
            except BaseException:db.rollback();raise
    def read(self,operation_id):
        operation_value(operation_id)
        # A NEW read-only connection, not the write return or in-memory proposal.
        with closing(sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True,timeout=.2)) as db:
            db.row_factory=sqlite3.Row
            row=db.execute('SELECT * FROM items WHERE operation_id=?',(operation_id,)).fetchone()
            return dict(row) if row else None
    def list_items(self):
        with closing(sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True,timeout=.2)) as db:
            db.row_factory=sqlite3.Row
            return [dict(row) for row in db.execute('SELECT * FROM items ORDER BY created_utc,operation_id LIMIT 100')]
