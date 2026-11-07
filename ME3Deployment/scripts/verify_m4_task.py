"""Independent read-only SQL verification outside task MCP implementation; no payload output."""
import argparse,hashlib,json,re,sqlite3,sys
from contextlib import closing
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1];DB=ROOT/'data/shopping.sqlite3'
p=argparse.ArgumentParser();p.add_argument('--baseline',action='store_true');p.add_argument('--run');p.add_argument('--expected-item',default='notebooks');p.add_argument('--expected-quantity',type=int,default=2);args=p.parse_args()
result={'at_utc':datetime.now(timezone.utc).isoformat(),'method':'independent standalone read-only SQLite query; no task MCP/store code imported','database':'data/shopping.sqlite3','database_exists':DB.exists(),'raw_payload_retained':False}
if args.baseline:
    if DB.exists():
        with closing(sqlite3.connect(DB.as_uri()+'?mode=ro',uri=True,timeout=.2)) as db:result['row_count']=db.execute('SELECT COUNT(*) FROM items').fetchone()[0]
    else:result['row_count']=0;result['basis']='Database file absent; no list records exist'
    print(json.dumps(result,indent=2));raise SystemExit(0)
if not args.run or not re.fullmatch(r'runtime-[0-9T-]+',args.run):raise SystemExit('Supply exact M4 runtime name')
rows=[json.loads(x) for x in (ROOT/'evidence/m4'/args.run/'events.jsonl').read_text().splitlines()]
verified=[x for x in rows if x['event']=='task_independently_verified']
if not verified:raise SystemExit('No in-agent verified operation to inspect')
event=verified[-1];operation=event['operation_id']
with closing(sqlite3.connect(DB.as_uri()+'?mode=ro',uri=True,timeout=.2)) as db:
    db.row_factory=sqlite3.Row
    row=db.execute('SELECT operation_id,item,quantity,created_utc FROM items WHERE operation_id=?',(operation,)).fetchone()
    count=db.execute('SELECT COUNT(*) FROM items').fetchone()[0]
row=dict(row) if row else None
state_digest=hashlib.sha256(json.dumps(row,sort_keys=True,separators=(',',':')).encode()).hexdigest()
result.update(operation_id=operation,row_count=count,operation_exists=row is not None,item_matches_expected=bool(row and row['item']==args.expected_item),quantity_matches_expected=bool(row and row['quantity']==args.expected_quantity),state_digest=state_digest,digest_matches_in_agent_readback=state_digest==event['state_digest'],database_sha256=hashlib.sha256(DB.read_bytes()).hexdigest(),run=args.run)
passed=all(result[x] for x in ('operation_exists','item_matches_expected','quantity_matches_expected','digest_matches_in_agent_readback'))
result['status']='PASS' if passed else 'FAIL'
print(json.dumps(result,indent=2));raise SystemExit(0 if passed else 2)
