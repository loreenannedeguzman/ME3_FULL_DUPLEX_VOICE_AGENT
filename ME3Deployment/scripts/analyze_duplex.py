"""Analyze local callback/VAD/cancellation metadata; no acoustic latency claims."""
from pathlib import Path
from collections import Counter
from datetime import datetime, timezone
import argparse, json

p=argparse.ArgumentParser(description=__doc__)
p.add_argument("--run")
args=p.parse_args()
root=Path(__file__).resolve().parents[1]
base=root/"evidence"/"m2"
if args.run:
    run=(base/args.run).resolve()
    if run.parent!=base.resolve(): raise SystemExit("Run outside M2 evidence")
else:
    run=max((p for p in base.glob("runtime-*") if p.is_dir()),key=lambda p:p.stat().st_mtime_ns)
rows=[json.loads(line) for line in (run/"events.jsonl").read_text().splitlines()]
counts=dict(Counter(r["event"] for r in rows))
clear_reports=[]
for clear in (r for r in rows if r["event"]=="playout_clear"):
    at=clear["monotonic_ns"]
    epoch=clear["epoch"]
    before=[r for r in rows if r["event"]=="playout_callback"
            and r.get("played_bytes",0)>0 and r["callback_ns"]<=at]
    ack=next((r for r in rows if r["event"]=="callback_epoch_observed"
              and r["epoch"]>=epoch and r["callback_ns"]>=at),None)
    capture=next((r for r in rows if r["event"]=="capture_callback"
                  and r["monotonic_ns"]>at and r["capture_count"]>clear["capture_count"]),None)
    generation=next((r for r in reversed(rows)
                     if r["event"]=="response_generation" and r.get("admitted")
                     and r["monotonic_ns"]<at),None)
    fresh=next((r for r in rows if r["event"]=="response_generation"
                and r.get("admitted") and r["monotonic_ns"]>at),None)
    clear_reports.append({
      "epoch":epoch,"clear_monotonic_ns":at,"discarded_callback_bytes":clear["discarded_bytes"],
      "capture_at_clear":clear["capture_count"],
      "next_capture_count":capture["capture_count"] if capture else None,
      "callback_ack_delay_ms":(ack["callback_ns"]-at)/1e6 if ack else None,
      "last_nonzero_callback_before_clear_ms":(at-before[-1]["callback_ns"])/1e6 if before else None,
      "previous_response_generation":generation["generation"] if generation else None,
      "next_response_generation":fresh["generation"] if fresh else None,
      "capture_continues":capture is not None,
      "callback_changed_epoch":ack is not None,
    })
result={
 "timestamp":datetime.now(timezone.utc).isoformat(),"run":run.name,"events":len(rows),
 "event_counts":counts,"clears":clear_reports,
 "config":[r for r in rows if r["event"]=="duplex_configuration"],
 "transport_close":[r for r in rows if r["event"]=="duplex_transport_closed"],
 "session_end":[r for r in rows if r["event"]=="session_end"],
 "completed_response_frames":sum(r.get("frame_type")=="LLMFullResponseEndFrame"
                                  and r.get("stage")=="provider_output" for r in rows),
 "scope":"Same-host monotonic software/callback evidence; operator must confirm audible overlap, stop and recovery. Scripted clears are not voice evidence.",
}
print(json.dumps(result,indent=2))
