"""Deterministic reducer: no providers, audio, raw transcripts or zero-imputed intervals."""
import collections,hashlib,json,math,statistics
from benchmark_m6.protocol import TRIALS,METHODS,VERSION
from benchmark_m6.storage import read,rows

def stamp(row):return row.get('observed_ns',row.get('callback_ns',row['monotonic_ns']))
def duration(start,end):
    if start is None or end is None:return None
    value=(stamp(end)-stamp(start))/1e6
    return value if value>=0 else None

def stats(values):
    values=sorted(x for x in values if x is not None and math.isfinite(x))
    return {'n':len(values),'median_ms':statistics.median(values) if values else None,'p95_ms':values[math.ceil(.95*len(values))-1] if len(values)>=20 else None,'p95_method':'nearest-rank if n>=20; coarse tail and clustered sessions','min_ms':min(values) if values else None,'max_ms':max(values) if values else None}

def analyze(folder):
    all_rows=[];native={};hashes={}
    for session in 'ABCDEF':
        path=folder/session/'events.jsonl';data=rows(path)
        if path.exists():hashes[session]=hashlib.sha256(path.read_bytes()).hexdigest()
        for row in data:row.setdefault('session_id',session)
        all_rows+=data
        meta=folder/session/'process-exit.json'
        if meta.exists():native[session]=read(meta)['native_exit']
    trial_ids=list(TRIALS)
    for row in all_rows:
        if row.get('event')=='m6_incidental_trial' and row['trial_id'] not in trial_ids:trial_ids.append(row['trial_id'])
    trials=[];attempts=[];interruptions=[];cold=[]
    for session in 'ABCDEF':
        data=[r for r in all_rows if r['session_id']==session]
        by_gen=collections.defaultdict(list)
        for row in data:
            if type(row.get('generation')) is int:by_gen[row['generation']].append(row)
        output=[r for r in data if r['event']=='m6_output_submission']
        latency=next((r.get('reported_output_latency_seconds') for r in data if r['event']=='output_device_open'),None)
        for generation,genrows in by_gen.items():
            def one(event):return next((r for r in genrows if r['event']==event),None)
            onset=one('m6_speech_onset_proxy');endpoint=one('m6_endpoint')
            tts=one('tts_backend_metrics')
            if generation==0:
                if tts:cold.append({'session':session,'kind':'cold_greeting','backend_metrics':{k:v for k,v in tts.items() if k in {'backend_first_pcm_seconds','backend_complete_seconds','cold_start','worker_epoch','reuse_index','model_import_seconds','model_load_seconds'}}})
                continue
            if onset is None:continue
            submission=next((r for r in output if r['epoch']==generation and r['nonzero'] and stamp(r)>=stamp(onset)),None)
            item={'session':session,'generation':generation,'trial_id':onset['trial_id'],'onset_ns':stamp(onset),'endpoint_ns':stamp(endpoint) if endpoint else None,'first_submission_ns':stamp(submission) if submission else None,'speech_end_endpoint_to_submission_ms':duration(endpoint,submission),'speech_onset_vad_proxy_to_submission_ms':duration(onset,submission),'reasoning_request_ms':duration(one('m6_reasoning_request_start'),one('m6_reasoning_request_end')),'agent_policy_or_reasoning_ms':duration(one('reasoning_start'),one('reasoning_end')),'tts_cold_start':tts.get('cold_start') if tts else None,'tts_backend_first_pcm_ms':tts.get('backend_first_pcm_seconds',0)*1000 if tts else None,'response_complete':one('response_complete') is not None,'asr_ms':None,'tts_ms':None,'canceled':any(r['event'] in {'mcp_request_cancelled','turn_task_cancelled','response_task_cancelled'} for r in genrows),'errors':[r['event'] for r in genrows if r['event'] in {'turn_error','mcp_request_error','m6_reasoning_request_error'}]}
            for role in ('asr','tts'):
                starts=[r for r in genrows if r['event']=='mcp_request_start' and r['role']==role]
                if starts:
                    start=starts[0];end=next((r for r in data if r.get('request_id')==start['request_id'] and r['event']=='mcp_request_end'),None);item[role+'_ms']=duration(start,end)
            attempts.append(item)
            cancellation=next((r for r in data if r['event']=='m6_cancellation_request' and r.get('replacement_generation')==generation),None)
            old=cancellation.get('generation') if cancellation else None
            old_started=next((r for r in data if r['event']=='generation_playout_started' and r['generation']==old and stamp(r)<=stamp(onset)),None)
            old_done=any(r['event']=='generation_playout_submitted' and r['generation']==old and stamp(r)<=stamp(onset) for r in data)
            if old_started and not old_done:
                flush=next((r for r in data if r['event']=='playout_clear' and r['epoch']==generation),None)
                ack=next((r for r in output if r['epoch']>=generation and flush and stamp(r)>=stamp(flush)),None)
                prior=[r for r in output if r['epoch']==old and r['nonzero']]
                last=max(prior,key=stamp) if prior else None
                estimate=((stamp(last)/1e9+last['block_seconds']+latency)-stamp(onset)/1e9)*1000 if last and type(latency) in (float,int) and 0<=latency<=1 else None
                interruptions.append({'session':session,'trial_id':onset['trial_id'],'old_generation':old,'replacement_generation':generation,'onset_method':'Silero SPEAKING proxy','onset_to_cancellation_request_ms':duration(onset,cancellation),'onset_to_queue_flush_ms':duration(onset,flush),'onset_to_callback_epoch_stop_proxy_ms':duration(onset,ack),'onset_to_estimated_old_pcm_drain_ms':estimate,'last_old_pcm_submission_ns':stamp(last) if last else None,'reported_output_latency_seconds':latency,'physically_measured_onset_to_stop_ms':None,'last_physically_audible_output_ns':None,'old_output_resubmitted_after_flush':any(r['epoch']==old and r['nonzero'] and flush and stamp(r)>stamp(flush) for r in output),'old_response_restarted':any(r['event']=='generation_playout_started' and r['generation']==old and stamp(r)>stamp(onset) for r in data),'old_completion_after_cancel':any(r['event']=='response_complete' and r['generation']==old and stamp(r)>stamp(onset) for r in data),'capture_continues':bool(flush and any(r['event']=='capture_callback' and r['capture_count']>flush['capture_count'] for r in data))})
    for trial_id in trial_ids:
        plan=TRIALS.get(trial_id,{'kind':'incidental','session':trial_id[0]});data=[r for r in all_rows if r.get('trial_id')==trial_id]
        start=next((r for r in data if r['event'] in {'m6_trial_start','m6_incidental_trial'}),None)
        report=next((r for r in reversed(data) if r['event']=='m6_trial_report'),None)
        a=[x for x in attempts if x['trial_id']==trial_id];intr=[x for x in interruptions if x['trial_id']==trial_id]
        errors=[r['event'] for r in data if r['event'] in {'turn_error','mcp_request_error','task_failure','pipeline_fault'}]
        timeout=any(r['event']=='m6_trial_timeout' for r in data)
        verdict='unstarted' if start is None else 'timeout' if timeout else report['outcome'] if report else 'incomplete'
        checks={'operator_pass':bool(report and report['outcome']=='pass'),'audible':bool(report and report.get('audible')),'response_correct':bool(report and report.get('response_correct')),'no_service_errors':not errors,'latest_reply_complete':bool(a and a[-1]['response_complete'])}
        if plan['kind']=='normal':checks['single_detected_utterance']=len(a)==1
        if plan['kind']=='interrupt':
            checks.update(two_actual_utterances=len(a)>=2,software_interruption_observed=bool(intr),operator_overlap=bool(report and report.get('overlap')),operator_stop=bool(report and report.get('old_stopped')),no_replay=bool(report and report.get('no_replay')),capture_and_fence=bool(intr and all(x['capture_continues'] and not x['old_response_restarted'] and not x['old_completion_after_cancel'] for x in intr)))
        if plan['kind'].startswith('split_'):checks.update(two_actual_segments=len(a)>=2,no_replay=bool(report and report.get('no_replay')))
        task_timing=None
        if plan['kind']=='task':
            verification=next((r for r in data if r['event']=='m6_task_external_verification'),None)
            committed=next((r for r in data if r['event']=='task_mcp_request_end' and r['tool']=='commit_item'),None)
            checks.update(real_commit=committed is not None,independent_verified=bool(verification and verification.get('verified')),human_confirmation=any(r['event']=='task_human_confirmation' for r in data))
            onset=next((r for r in data if r['event']=='m6_speech_onset_proxy'),None)
            task_timing={'speech_onset_proxy_to_commit_ms':duration(onset,committed),'speech_onset_proxy_to_independent_verification_ms':duration(onset,verification),'commit_to_independent_verification_ms':duration(committed,verification),'includes_human_confirmation_and_operator_control_wait':True}
        if verdict=='pass' and not all(checks.values()):verdict='fail'
        trials.append({'trial_id':trial_id,'session':plan['session'],'kind':plan['kind'],'case':plan.get('case'),'outcome':verdict,'checks':checks,'attempt_count':len(a),'attempt_generations':[x['generation'] for x in a],'canceled_attempts':sum(x['canceled'] for x in a),'timeout':timeout,'errors':errors,'task_timing':task_timing})
    counts=collections.Counter(x['outcome'] for x in trials);started=sum(x['outcome']!='unstarted' for x in trials)
    ordinary_ids={x['trial_id'] for x in trials if x['kind']=='normal' and x['attempt_count']==1}
    steady=[x for x in attempts if x['trial_id'] in ordinary_ids and x['tts_cold_start'] is False]
    successful_ids={x['trial_id'] for x in trials if x['outcome']=='pass'}
    fields=['speech_end_endpoint_to_submission_ms','speech_onset_vad_proxy_to_submission_ms','asr_ms','reasoning_request_ms','tts_ms','agent_policy_or_reasoning_ms','tts_backend_first_pcm_ms']
    split=[x for x in trials if x['kind'].startswith('split_') and x['outcome']!='unstarted']
    failure=sum(counts[x] for x in ('fail','timeout','incomplete'))
    kinds={}
    for kind in sorted({x['kind'] for x in trials}):
        subset=[x for x in trials if x['kind']==kind];started_kind=[x for x in subset if x['outcome']!='unstarted'];failed_kind=sum(x['outcome'] in {'fail','timeout','incomplete'} for x in started_kind)
        kinds[kind]={'scheduled':len(subset),'started':len(started_kind),'outcomes':dict(collections.Counter(x['outcome'] for x in subset)),'failure_rate_of_started':failed_kind/len(started_kind) if started_kind else None}
    return {'protocol':VERSION,'methods':METHODS,'raw_event_hashes':hashes,'accounting_by_kind':kinds,'trial_accounting':{'planned':len(TRIALS),'incidental':sum(x['kind']=='incidental' for x in trials),'total':len(trials),'started':started,'outcomes':dict(counts),'failure_timeout_incomplete_rate_of_started':failure/started if started else None,'unstarted_rate_of_planned':counts['unstarted']/len(TRIALS),'timeout_rate_of_started':sum(x['timeout'] for x in trials)/started if started else None,'trials_with_canceled_attempt_rate':sum(x['canceled_attempts']>0 for x in trials if x['outcome']!='unstarted')/started if started else None,'incomplete_rate_of_started':counts['incomplete']/started if started else None},'steady_normal_all_available_including_semantic_failures':{k:stats([x[k] for x in steady]) for k in fields},'steady_normal_successful_only':{k:stats([x[k] for x in steady if x['trial_id'] in successful_ids]) for k in fields},'cold_greeting_provider_startup':cold,'retained_warmups':[x for x in attempts if TRIALS.get(x['trial_id'],{}).get('kind')=='warmup'],'restarted_model_attempts':[x for x in attempts if x['tts_cold_start'] is True],'split_recovery':{'started':len(split),'successes':sum(x['outcome']=='pass' for x in split),'success_rate':sum(x['outcome']=='pass' for x in split)/len(split) if split else None},'interruption_summary':{k:stats([x[k] for x in interruptions]) for k in ('onset_to_cancellation_request_ms','onset_to_queue_flush_ms','onset_to_callback_epoch_stop_proxy_ms','onset_to_estimated_old_pcm_drain_ms')},'interruption_observations':interruptions,'physical_acoustic_metrics':{'status':'NOT_MEASURED','speech_onset':None,'speech_end':None,'first_audible_agent_audio':None,'last_audible_output':None},'native_exit_codes':native,'trials':trials,'attempts':attempts,'limitations':['One operator, fixed repeated scripts, clustered sessions; descriptive baseline, not population tail confidence','VAD onset/endpoint proxies include detector and input-queue delay, not physical phoneme boundaries','Callback return-ready precedes DAC/acoustics; PortAudio drain is only an estimate','No waveform/transcript retained; operator reports establish semantic/acoustic categories, not calibrated times','M5 semantic ASR and budget failures remain in prior evidence, not silently added to new-trial denominator']}
