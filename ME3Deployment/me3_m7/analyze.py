"""Same M6 metrics with explicit trace decomposition and matched-script comparison."""
import collections,hashlib,json
from pathlib import Path
import benchmark_m6.analyze as original
import benchmark_m6.analyze_v2 as v2
from benchmark_m6.analyze import stats
from benchmark_m6.storage import read,rows
ROOT=Path(__file__).resolve().parents[1]
def duration(start,end):
    if start is None or end is None:return None
    def clock(row):
        for key in ('observed_ns','callback_ns','monotonic_ns'):
            if key in row:return row[key]
        raise ValueError('missing_monotonic_measurement')
    value=(clock(end)-clock(start))/1e6
    return value if value>=0 else None
def comparable(summary):
    ids={t['trial_id'] for t in summary['trials'] if t['kind']=='normal' and t['attempt_count']==1 and t['outcome']=='pass'}
    return {a['trial_id']:a for a in summary['attempts'] if a['trial_id'] in ids and a['tts_cold_start'] is False and a['response_complete']}
def matched_comparison(baseline,current):
    base=comparable(baseline);new=comparable(current);matched=sorted(set(base)&set(new));fields=('asr_ms','speech_end_endpoint_to_submission_ms','speech_onset_vad_proxy_to_submission_ms','reasoning_request_ms','tts_ms')
    return {'definition':'same normal trial ID/script, each one detected utterance, warm TTS, operator-correct completed reply; missing/failed trials separately retained','baseline_eligible_ids':sorted(base),'matched_ids':matched,'unmatched_baseline_ids':sorted(set(base)-set(new)),'all_baseline_cases_matched':set(base)<=set(new) and bool(base),'n':len(matched),'metrics':{k:{'m6':stats([base[t][k] for t in matched]),'m7':stats([new[t][k] for t in matched]),'paired_delta_m7_minus_m6':stats([new[t][k]-base[t][k] for t in matched if new[t][k] is not None and base[t][k] is not None])} for k in fields},'per_trial':[{'trial_id':t,'m6_speech_proxy_seconds':(base[t]['endpoint_ns']-base[t]['onset_ns'])/1e9,'m7_speech_proxy_seconds':(new[t]['endpoint_ns']-new[t]['onset_ns'])/1e9,'m6_asr_ms':base[t]['asr_ms'],'m7_asr_ms':new[t]['asr_ms'],'m6_end_to_submission_ms':base[t]['speech_end_endpoint_to_submission_ms'],'m7_end_to_submission_ms':new[t]['speech_end_endpoint_to_submission_ms']} for t in matched],'limitations':['Same scripts/voice/VAD boundaries, different human performances/time/network state; not randomized causal trial','All31 ledger and failures retained; comparison is conditional, not an unconditional latency or proof from a smaller workload']}
def analyze(folder):
    folder=Path(folder);extra={};extra_hashes={}
    for session in 'ABCDEF':
        path=folder/session/'operator-after-exit.jsonl'
        if path.exists():extra[session]=rows(path);extra_hashes[session]=hashlib.sha256(path.read_bytes()).hexdigest()
    # A read-only analysis view joins genuine separately saved operator reports; original event files are never changed.
    def joined(path):
        data=rows(path)
        if Path(path).name=='events.jsonl':
            ids={r.get('trial_id') for r in data if r['event']=='m6_trial_report'}
            data += [r for r in extra.get(Path(path).parent.name,[]) if r['trial_id'] not in ids]
        return data
    old_original,old_v2=original.rows,v2.rows
    try:
        original.rows=joined;v2.rows=joined;result=v2.analyze(folder)
    finally:original.rows=old_original;v2.rows=old_v2
    trace=[];hashes={}
    for session in 'ABCDEF':
        path=folder/session/'asr-server.stderr.log'
        if not path.exists():continue
        hashes[session]=hashlib.sha256(path.read_bytes()).hexdigest();main=rows(folder/session/'events.jsonl');clock=next((x for x in main if x['event']=='m6_clock'),None)
        server=[]
        for line in path.read_text(encoding='utf-8').splitlines():
            try:row=json.loads(line)
            except ValueError:continue
            if str(row.get('event','')).startswith('m7_'):server.append(row)
        by_request=collections.defaultdict(list)
        for row in server:
            if row.get('request_id'):by_request[row['request_id']].append(row)
        for rid,data in by_request.items():
            def one(name):return next((r for r in data if r['event']=='m7_'+name),None)
            received=one('server_received');g=received.get('generation') if received else None
            dispatch=next((r for r in main if r['event']=='m7_client_dispatch' and r['request_id']==rid),None);end=next((r for r in main if r['event']=='mcp_request_end' and r['request_id']==rid),None)
            same_boot=bool(clock and received and received.get('boot_id')==clock.get('boot_id'))
            if not same_boot:raise ValueError('ASR_cross_process_clock_boot_mismatch')
            pairs={'dispatch_to_server_tool_entry_ms':(dispatch,received),'server_validation_ms':(received,one('validation_complete')),'queue_ms':(one('queue_wait_start'),one('queue_wait_end')),'provider_construction_ms':(one('provider_create_start'),one('provider_create_end')),'payload_encoding_ms':(one('wav_encode_start'),one('wav_encode_end')),'provider_generate_ms':(one('provider_request_start'),one('provider_response')),'http_start_to_headers_ms':(one('http_request_start'),one('http_response_headers')),'headers_to_provider_result_ms':(one('http_response_headers'),one('provider_response')),'connection_create_ms':(one('transport_connection_create_start'),one('transport_connection_create_end')),'dns_ms':(one('transport_dns_resolvehost_start'),one('transport_dns_resolvehost_end')),'headers_issued_to_response_headers_ms':(one('transport_request_headers_sent'),one('http_response_headers')),'resource_release_ms':(one('resource_release_start'),one('resource_released')),'server_return_to_client_validation_ms':(one('result_returned'),end),'cancellation_observed_to_resource_release_ms':(one('server_cancellation_observed'),one('resource_released'))}
            payload=one('validation_complete') or {};encoded=one('wav_encode_end') or {};terminal=one('provider_response') or one('provider_error') or one('provider_cancel_observed')
            trace.append({'session':session,'trial_id':dispatch.get('trial_id') if dispatch else None,'request_id':rid,'generation':g,'same_boot':True,'pcm_bytes':payload.get('pcm_bytes'),'previous_pcm_bytes':payload.get('previous_pcm_bytes'),'audio_seconds':payload.get('audio_seconds'),'sample_rate':payload.get('sample_rate'),'channels':payload.get('channels'),'provider_format':encoded.get('audio_format'),'encoded_current_bytes':encoded.get('wav_bytes'),'http_wire_attempts':sum(x['event']=='m7_http_request_start' for x in data),'hidden_retry_blocked':one('sdk_hidden_retry_blocked') is not None,'provider_elapsed_to_terminal_ms':duration(one('provider_request_start'),terminal),'provider_terminal_event':terminal.get('event') if terminal else None,'worker_completed':one('worker_completed') is not None,'cleanup_ok':(one('resource_released') or {}).get('cleanup_ok'),**{k:duration(*v) for k,v in pairs.items()}})
    main_rows=[r for session in 'ABCDEF' for r in rows(folder/session/'events.jsonl')]
    result['m7_failure_causes']={'asr_request_timeouts':sum(r['event']=='mcp_request_error' and r.get('role')=='asr' and r.get('error_type') in {'TimeoutError','ReadTimeout'} for r in main_rows),'benchmark_turn_watchdog_events':sum(r['event']=='m6_trial_timeout' for r in main_rows),'watchdog_trials':sorted({r.get('trial_id') for r in main_rows if r['event']=='m6_trial_timeout'}),'note':'Inherited outcome timeout includes turn watchdogs; it does not alone establish a provider ASR timeout.'}
    result['m7_asr_trace']={'per_request':trace,'stage_summary_available_observations':{k:stats([t[k] for t in trace]) for k in trace[0] if k.endswith('_ms')} if trace else {},'stderr_sha256':hashes,'measurement_limits':'HTTP interval combines connect/upload/network/provider; body-submission trace is application-level, not acknowledged network delivery. No remote compute cancellation/credit guarantee.'}
    baseline_path=ROOT/'evidence/m6/run-20261020-001/summary-corrected-001.json'
    if not baseline_path.exists():baseline_path=ROOT/'evidence/m6/pi-copy-002/run-20261020-001/summary-corrected-001.json'
    result['m6_matched_comparison']=matched_comparison(read(baseline_path),result) if baseline_path.exists() else {'status':'BASELINE_NOT_AVAILABLE'}
    result['m7_operator_after_exit_sha256']=extra_hashes;result['protocol']='m7-v1.0; M6 scripts and timing boundaries preserved'
    result['m7_limitations']=['Conservative grammar joins only supported continuation; arbitrary ambiguous continuations require full repetition','First/last captured segment times are VAD frame-arrival proxies, not physical acquisition or phoneme boundaries','Timeout continuation policy differs from M6: at most2released failures per session, no automatic retry','Prompt semantic answers/operator observations do not constitute independently transcribed acoustic WER']
    return result
