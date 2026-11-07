"""Analyze MCP provenance, generation cancellation and callback metadata only."""
import argparse,json,re
from pathlib import Path
from collections import Counter
from m3_evidence import callback_continuity
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1];base=ROOT/'evidence/m3'
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run');p.add_argument('--copy');args=p.parse_args()
if args.copy:
    if not re.fullmatch(r'(pi|provider)-copy-[0-9]{3}',args.copy):raise SystemExit('Invalid evidence copy')
    base=base/args.copy
run=(base/args.run).resolve() if args.run else max(base.glob('runtime-*'),key=lambda x:x.stat().st_mtime_ns)
if run.parent.resolve()!=base.resolve():raise SystemExit('Run outside M3 evidence')
rows=[json.loads(x) for x in (run/'events.jsonl').read_text().splitlines()]
counts=Counter(x['event'] for x in rows)
def last_before(event,at):return next((x for x in reversed(rows) if x['event']==event and x['monotonic_ns']<=at),None)
def next_after(event,at):return next((x for x in rows if x['event']==event and x['monotonic_ns']>at),None)
clears=[]
for clear in [x for x in rows if x['event']=='playout_clear']:
    at=clear['monotonic_ns'];speech=last_before('local_speech_start',at);cancel=last_before('turn_cancelled',at)
    prior=last_before('generation_playout_started',at)
    finished=bool(prior and any(x['event']=='generation_playout_submitted' and x['generation']==prior['generation'] and x['monotonic_ns']<=at for x in rows))
    before=[x for x in rows if x['event']=='playout_callback' and x.get('played_bytes',0)>0 and x['callback_ns']<=at]
    ack,cap=callback_continuity(clear,[x for x in rows if x['event']=='callback_epoch_observed'],[x for x in rows if x['event']=='capture_callback'])
    fresh=next_after('generation_playout_started',at)
    clears.append({'epoch':clear['epoch'],'wall_time':clear['wall_time'],'local_speech_generation':speech['generation'] if speech else None,'cancelled_generation':cancel['generation'] if cancel else None,'pending_work_cancelled':cancel['pending_work'] if cancel else None,'prior_playout_generation':prior['generation'] if prior else None,'prior_playout_finished_before_clear':finished,'discarded_bytes':clear['discarded_bytes'],'capture_at_clear':clear['capture_count'],'next_capture_count':cap['capture_count'] if cap else None,'capture_continues':cap is not None,'callback_epoch_acknowledged':ack is not None,'callback_ack_delay_ms':None,'clock_note':'causal epoch/capture, delayed log writes; no acoustic timing','last_nonzero_callback_before_clear_ms':(at-before[-1]['callback_ns'])/1e6 if before else None,'replacement_playout_generation':fresh['generation'] if fresh else None})
requests=[]
for start in [x for x in rows if x['event']=='mcp_request_start']:
    result=next((x for x in rows if x.get('request_id')==start['request_id'] and x['event'] in {'mcp_request_end','mcp_request_error','mcp_request_cancelled'}),None)
    requests.append({'role':start['role'],'generation':start['generation'],'request_id':start['request_id'],'outcome':result['event'] if result else 'PENDING','elapsed_ms':(result['monotonic_ns']-start['monotonic_ns'])/1e6 if result else None,'server_pid':result.get('server_pid') if result else None,'error_type':result.get('error_type') if result else None,'code':result.get('code') if result else None})
result={'analyzed_at_utc':datetime.now(timezone.utc).isoformat(),'run':run.name,'event_counts':dict(counts),'mcp_servers':[x for x in rows if x['event']=='mcp_connected'],'configuration':[x for x in rows if x['event']=='m3_configuration'],'session_start':[x for x in rows if x['event']=='session_start'],'session_end':[x for x in rows if x['event']=='session_end'],'session_stop':[x for x in rows if x['event']=='session_stop'],'transport_close':[x for x in rows if x['event']=='duplex_transport_closed'],'requests':requests,'clears':clears,'completed_replies':[x for x in rows if x['event']=='response_complete' and not x.get('greeting') and not x.get('error_notice')],'errors':[x for x in rows if x['event'] in {'turn_error','mcp_request_error'}],'scope':'Metadata only. Human must confirm acoustic overlap, old output stopped/no resumption, coherent new answers/context. Callback timing is not acoustic latency; synthetic probes are not live conversation.'}
print(json.dumps(result,indent=2))
