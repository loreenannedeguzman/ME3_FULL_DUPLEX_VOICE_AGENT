"""Reproduce focused functional evidence; no provider, audio, DB mutation or benchmark run."""
import argparse,collections,hashlib,json,statistics
from pathlib import Path

def rows(path):
    if not path.exists():return []
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.startswith('{')]
def stamp(row):return row.get('observed_ns',row.get('monotonic_ns'))
def duration(items,start,end):
    a=next((x for x in items if x['event']==start),None);b=next((x for x in items if x['event']==end),None)
    return (stamp(b)-stamp(a))/1e9 if a and b and stamp(b)>=stamp(a) else None
def describe(values):
    values=[v for v in values if v is not None]
    return {'n':len(values),'min_seconds':min(values) if values else None,'median_seconds':statistics.median(values) if values else None,'max_seconds':max(values) if values else None,'scope':'lightweight mixed-workload diagnostics, not comparable standardized benchmark or acoustic latency'}
def analyze(root):
    run=root/'core-20261020-001';sessions={};requests=[];interruptions=[];resources=[];inputs={}
    for p in sorted(run.rglob('*')):
        if p.is_file():inputs[p.relative_to(root).as_posix()]=hashlib.sha256(p.read_bytes()).hexdigest()
    for name in 'ABCDE':
        folder=run/name;events=rows(folder/'events.jsonl');server=rows(folder/'asr-server.stderr.log');operator=rows(folder/'operator.jsonl');counts=collections.Counter(x['event'] for x in server);parent_counts=collections.Counter(x['event'] for x in events)
        sessions[name]={'operator_reports':operator,'native_exit':json.loads((folder/'process-exit.json').read_text())['native_exit'],'server_counts':dict(counts),'event_counts':dict(parent_counts),'turn_outcomes':dict(collections.Counter(x['outcome'] for x in events if x['event']=='core_turn_settled')),'segment_decisions':[{'generation':x['generation'],'decision':x['decision'],'episode_id':x.get('episode_id')} for x in events if x['event']=='m7_segment_decision'],'list_readbacks':[x for x in events if x['event']=='core_list_readback'],'task_route_metadata':[x for x in events if x['event'] in {'core_task_route_shape','core_trial_proposal_validation','core_task_only_clarification'}]}
        for start in [x for x in server if x['event']=='m7_server_received']:
            rid=start['request_id'];items=[x for x in server if x.get('request_id')==rid];parent=[x for x in events if x.get('request_id')==rid]
            release=next((x for x in items if x['event']=='m7_resource_released'),{})
            requests.append({'session':name,'generation':start['generation'],'request_id':rid,'mcp_seconds':duration(parent,'mcp_request_start','mcp_request_end'),'server_result_seconds':duration(items,'m7_server_received','m7_result_ready'),'queue_seconds':duration(items,'m7_queue_wait_start','m7_queue_wait_end'),'encode_seconds':duration(items,'m7_wav_encode_start','m7_wav_encode_end'),'provider_round_trip_seconds':duration(items,'m7_provider_request_start','m7_provider_response'),'dns_seconds':duration(items,'m7_transport_dns_resolvehost_start','m7_transport_dns_resolvehost_end'),'connection_seconds':duration(items,'m7_transport_connection_create_start','m7_transport_connection_create_end'),'http_until_headers_seconds':duration(items,'m7_http_request_start','m7_http_response_headers'),'wire_attempts':sum(x['event']=='m7_http_request_start' for x in items),'connection_reused':any(x['event']=='m7_core_transport_connection_reused' for x in items),'result_ready':any(x['event']=='m7_result_ready' for x in items),'cleanup_ok':release.get('cleanup_ok',False),'retired':any(x['event']=='m7_core_pool_retired' for x in items),'cancel_metadata':[x['event'] for x in items if 'cancel' in x['event'] or 'timeout' in x['event'] or 'deadline' in x['event']]})
        for cancel in [x for x in events if x['event']=='turn_task_cancelled' and x.get('stage')=='tts_or_playout']:
            g=cancel['generation'];onset=next((x for x in events if x['event']=='core_speech_onset' and x.get('generation')==g+1),None)
            clear=next((x for x in events if x['event']=='playout_clear' and x.get('epoch')==g+1 and stamp(x)>=stamp(cancel)),None)
            switched=next((x for x in events if x['event']=='core_callback_epoch' and x.get('epoch')==g+1 and stamp(x)>=stamp(cancel)),None)
            prior=any(x['event']=='core_first_submission' and x.get('epoch')==g and stamp(x)<stamp(cancel) for x in events)
            after=next((x for x in events if x['event']=='core_first_submission' and x.get('epoch')==g+1 and stamp(x)>stamp(cancel)),None)
            old_completed=any(x['event']=='response_complete' and x.get('generation')==g and stamp(x)>stamp(cancel) for x in events)
            interruptions.append({'session':name,'episode_id':cancel.get('episode_id'),'old_generation':g,'prior_nonzero_software_submission':prior,'onset_to_cancel_ms':(stamp(cancel)-stamp(onset))/1e6 if onset else None,'onset_to_queue_clear_ms':(stamp(clear)-stamp(onset))/1e6 if onset and clear else None,'onset_to_callback_epoch_ms':(stamp(switched)-stamp(onset))/1e6 if onset and switched else None,'discarded_bytes':clear.get('discarded_bytes') if clear else None,'capture_at_clear':clear.get('capture_count') if clear else None,'capture_at_replacement_submission':after.get('capture_count') if after else None,'capture_advanced':bool(clear and after and after['capture_count']>clear['capture_count']),'canceled_generation_completed_later':old_completed,'measurement':'software VAD/queue/callback proxies; audible stop and no resumption separately confirmed by operator; no acoustic measurement'})
        resources.extend(x for x in events if x['event']=='core_resource_sample')
    operators=[x for s in sessions.values() for x in s['operator_reports']]
    # Keep failures in their original cohort; successful supplementary task is not a replacement receipt.
    main=[x for x in operators if x['episode_id'][0] in 'ABC'];extra=[x for x in operators if x['episode_id'][0] in 'DE']
    by_session={}
    for name in 'ABCDE':
        samples=[x for x in rows(run/name/'events.jsonl') if x['event']=='core_resource_sample']
        by_session[name]={'samples':len(samples),'max_sampled_summed_rss_mib':max((x['sum_rss_kib']/1024 for x in samples),default=None),'limitation':'5second process RSS sums can double-count shared pages and miss peaks; not unique physical RAM'}
    audit=json.loads((root/'pi-final-audit-001.json').read_text())
    return {'mode':'focused core functional evidence; comprehensive benchmark deferred','analysis_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'raw_input_hashes':inputs,'source_revisions':{'ABC':json.loads((root/'source-001.json').read_text())['revision'],'D':json.loads((root/'source-002.json').read_text())['revision'],'E_final':json.loads((root/'source-003.json').read_text())['revision']},'main_cohort':{'planned':12,'reported':len(main),'outcomes':dict(collections.Counter(x['outcome'] for x in main))},'separate_task_followups':extra,'sessions':sessions,'asr_requests':requests,'asr_lifecycle':{'requests_received':len(requests),'results_ready':sum(x['result_ready'] for x in requests),'resources_released_ok':sum(x['cleanup_ok'] for x in requests),'wire_attempts':sum(x['wire_attempts'] for x in requests),'hidden_second_wire_requests':[x['request_id'] for x in requests if x['wire_attempts']>1],'requests_with_connection_reuse':sum(x['connection_reused'] for x in requests)},'diagnostics':{key:describe([x[key] for x in requests]) for key in ['mcp_seconds','queue_seconds','encode_seconds','provider_round_trip_seconds','dns_seconds','connection_seconds','http_until_headers_seconds']},'interruptions':interruptions,'resources':by_session,'budget':json.loads((run/'budget.json').read_text()),'task_verification':json.loads((run/'task-independent-verification.json').read_text()),'final_task_state':json.loads((run/'final-task-state-E.json').read_text()),'target_audit':audit,'limitations':['ABC acoustic gates were onsource001; D task guard trial failed on002; only essential-detail task was live onfinal003. ASR/voice/VAD/playback unchanged acrossthese revisions.','B1 first hesitation produced clarification; operator later repeated complete request and received tip. Original B1failure retained.','C3 exact rejected proposal field unknown: metadata beforematch diagnostics; no transcripts retained. D ordinary-route guard failed locally before text API.','Controlled100ms timeout retired local request/client; this is not a natural provider outage or proof remote computation stopped.','Four-row spoken read-back and new partial-task-grammar clarification have automated evidence only; no final-extra mic allowance for acoustic recheck.','No calibrated physical speech/onset/lastaudible timing, no recognition word-error audit, no naturalness comparison in this phase.','No standardized latency comparison or p95; heterogeneous focused sample and user/network variability preclude speedup claims.']}
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--raw',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--check',action='store_true');a=p.parse_args();data=analyze(a.raw)
    if a.check:assert json.loads(a.output.read_text())==data;print('EXACT_REPRODUCTION_PASS')
    else:
        with a.output.open('x',encoding='utf-8') as f:json.dump(data,f,indent=2)
        print(json.dumps({k:data[k] for k in ['main_cohort','asr_lifecycle','diagnostics','interruptions','resources','budget']},indent=2))
