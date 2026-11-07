"""Read-only fixed-text software timing/queue analysis; never acoustic latency/MOS."""
import argparse,json,statistics
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('folder');a=p.parse_args();root=Path(a.folder)
data=json.loads((root/'measurements.json').read_text());result={'scope':'Measured software durations; subjective quality separately operator-reported; no acoustic measurements','providers':{},'listening':[]}
for provider in ['flite','piper']:
    rows=[x for x in data['records'] if x['provider']==provider];valid=[x for x in rows if x['status']=='PASS'];cases={}
    for case in data['corpus']:
        values=[x for x in valid if x['case']==case];cases[case]={'n':len(values),'mcp_complete_pcm_seconds':[x['mcp_complete_pcm_seconds'] for x in values],'audio_duration_seconds':[x['audio_seconds'] for x in values],'backend_first_pcm_seconds':[x['backend_metrics'].get('backend_first_pcm_seconds') for x in values]}
    vals=[x['mcp_complete_pcm_seconds'] for x in valid]
    result['providers'][provider]={'attempts':len(rows),'completed':len(valid),'failed':len(rows)-len(valid),'fixed_case_mix_median_seconds':statistics.median(vals) if vals else None,'range_seconds':[min(vals),max(vals)] if vals else None,'p95':None,'per_case':cases,'full_scale_samples':sum(x['full_scale_samples'] for x in valid)}
for events in sorted(root.glob('listening-*/events.jsonl')):
    rows=[json.loads(x) for x in events.read_text().splitlines()]
    for start in [x for x in rows if x['event']=='paired_clip_start']:
        end=next((x for x in rows if x['event']=='paired_clip_end' and x['label']==start['label']),None)
        if end is None:continue
        callbacks=[x for x in rows if x['event']=='playout_callback' and start['monotonic_ns']<=x['monotonic_ns']<=end['monotonic_ns'] and x['played_bytes']>0]
        missed=sum(max(0,b['callback_count']-a['callback_count']-1) for a,b in zip(callbacks,callbacks[1:]))
        result['listening'].append({'path':str(events.relative_to(root)),'case':start['case'],'label':start['label'],'provider':start['provider'],'first_callback_submission_delay_seconds':(callbacks[0]['callback_ns']-start['monotonic_ns'])/1e9 if callbacks else None,'submit_and_drain_seconds':(end['monotonic_ns']-start['monotonic_ns'])/1e9,'submitted_bytes':sum(x['played_bytes'] for x in callbacks),'expected_source_mono_bytes':start['byte_count'],'output_format':'48000Hz stereo PCM16; bytes differ from24000Hz mono source','callbacks_with_audio':len(callbacks),'silent_callbacks_between_audio_callbacks':missed,'partial_internal_audio_callbacks':sum(0<x['played_bytes']<3840 for x in callbacks[1:-1]),'status_callbacks':sum(bool(x.get('status')) for x in callbacks),'physical_audibility':'OPERATOR_REQUIRED'})
print(json.dumps(result,indent=2))
