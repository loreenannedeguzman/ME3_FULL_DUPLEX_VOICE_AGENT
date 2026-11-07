"""Summarize metadata-only events; never reads audio, transcripts or .env."""
from pathlib import Path
from collections import Counter
from datetime import datetime, timezone
import json
root=Path(__file__).resolve().parents[1]
runs=[p for p in (root/"evidence"/"m1").glob("runtime-*") if p.is_dir()]
if not runs:
    raise SystemExit("No runtime event directory")
run=max(runs,key=lambda p:p.stat().st_mtime_ns)
rows=[]
invalid=0
for line in (run/"events.jsonl").read_text().splitlines():
    try:
        rows.append(json.loads(line))
    except json.JSONDecodeError:
        invalid+=1
result={
 "timestamp":datetime.now(timezone.utc).isoformat(),
 "run":run.name,"event_rows":len(rows),"incomplete_or_invalid_rows":invalid,
 "event_counts":dict(Counter(row.get("event") for row in rows)),
 "frame_counts_by_stage":dict(Counter(
     row.get("stage","")+":"+row.get("frame_type","")
     for row in rows if row.get("event")=="pipeline_frame"
 )),
 "session_start":[row for row in rows if row.get("event")=="session_start"],
 "session_end":[row for row in rows if row.get("event")=="session_end"],
 "scope":"Pipeline metadata, not acoustic confirmation or a latency benchmark",
}
print(json.dumps(result,indent=2))
