"""Reproduce software timing and all failures; never manufacture acoustic/reference metrics."""
import collections,hashlib,json,math,statistics
from pathlib import Path
from benchmark_m6.storage import rows,read
VERSION='final-analysis-v1'
def ns(row):return row.get('observed_ns',row.get('monotonic_ns'))
def seconds(a,b):return None if a is None or b is None else (ns(b)-ns(a))/1e9
def stats(values):
    v=sorted(x for x in values if x is not None)
    return {'n':len(v),'median_seconds':statistics.median(v) if v else None,'p95_seconds':v[math.ceil(.95*len(v))-1] if len(v)>=20 else None,'p95_method':'nearest rank only when n>=20; project reporting rule, not course threshold'}
def js_divergence(p,q):
    if not p or len(p)!=len(q) or any(type(x) not in (int,float) or not math.isfinite(x) or x<0 for x in [*p,*q]) or sum(p)<=0 or sum(q)<=0:raise ValueError('aligned_nonnegative_reference_histograms_required')
    p=[x/sum(p) for x in p];q=[x/sum(q) for x in q];m=[(a+b)/2 for a,b in zip(p,q)]
    return .5*sum(a*math.log2(a/c) for a,c in zip(p,m) if a)+.5*sum(b*math.log2(b/c) for b,c in zip(q,m) if b)
def stages(data):
    starts={};out=[]
    terminal={'mcp_request_end':'complete','mcp_request_cancelled':'canceled','mcp_request_error':'error','m6_reasoning_request_end':'complete','m6_reasoning_request_cancelled':'canceled','m6_reasoning_request_error':'error'}
    for x in sorted(data,key=ns):
        event=x['event']
        if event=='mcp_request_start':starts[('mcp',x['request_id'])]=x
        elif event=='m6_reasoning_request_start':starts[('reasoning',x['generation'])]=x
        elif event in terminal:
            key=('mcp',x['request_id']) if event.startswith('mcp_') else ('reasoning',x['generation']);start=starts.pop(key,None)
            if start is None:continue
            state='timeout' if x.get('error_type') in {'TimeoutError','ReadTimeout','ConnectTimeout','TimeoutException'} else terminal[event]
            out.append({'role':start.get('role','reasoning'),'generation':start.get('generation'),'request_id':start.get('request_id'),'state':state,'start_ns':ns(start),'end_ns':ns(x),'complete_seconds':seconds(start,x) if state=='complete' else None,'elapsed_to_terminal_seconds':seconds(start,x),'censored':state!='complete'})
    for start in starts.values():out.append({'role':start.get('role','reasoning'),'generation':start.get('generation'),'request_id':start.get('request_id'),'state':'incomplete','start_ns':ns(start),'end_ns':None,'complete_seconds':None,'elapsed_to_terminal_seconds':None,'censored':True})
    return out

def analyze_data(trials,data,task=None):
    entries=[];all_stages=[]
    for trial,plan in trials.items():
        own=[x for x in data if x.get('trial_id')==trial];start=next((x for x in own if x['event']=='m6_trial_start'),None);reports=[x for x in own if x['event']=='m6_trial_report']
        if len(reports)>1:raise ValueError('duplicate_operator_report')
        report=reports[0] if reports else None;state='unstarted' if start is None else report['outcome'] if report else 'incomplete'
        stage=stages(own);all_stages.extend({**x,'trial_id':trial,'kind':plan['kind']} for x in stage)
        errors=[x for x in stage if x['state'] in {'error','timeout','incomplete'}];generations=[x['generation'] for x in own if x['event']=='core_turn_started'];g=generations[-1] if generations else None
        audio=next((x for x in own if x['event']=='bf_audio_submission' and x.get('nonzero') and x.get('kind')=='response' and x.get('generation')==g and g is not None),None)
        selected_asr=next((x for x in reversed(stage) if x['role']=='asr' and x['generation']==g),None)
        dispatch=selected_asr['start_ns'] if selected_asr else None
        endpoints=[x for x in own if x['event']=='bf_endpoint' and (dispatch is None or ns(x)<=dispatch)];endpoint=endpoints[-1] if endpoints else None
        onsets=[x for x in own if x['event']=='bf_speech_onset'];onset=onsets[0] if onsets else None
        overlap=next((x for x in onsets if x.get('output_kind')=='response'),None)
        cancel=next((x for x in own if x['event']=='completion_takeover' and x.get('reason') in {'overlap_exceeds_brief_duration','recognized_substantive_overlap'} and (overlap is None or ns(x)>=ns(overlap))),None)
        suspended=next((x for x in own if x['event']=='completion_response_suspended' and (overlap is None or ns(x)>=ns(overlap))),None)
        stop_proxy=next((x for x in own if x['event']=='bf_audio_submission' and not x.get('nonzero') and suspended and ns(x)>=ns(suspended)),None)
        final_endpoint=next((x for x in reversed(own) if x['event']=='bf_endpoint'),None)
        early=bool(plan['kind'] in {'split_replace','split_complete'} and final_endpoint and any(x['event']=='bf_audio_submission' and x.get('nonzero') and x.get('kind')=='response' and ns(x)<ns(final_endpoint) for x in own))
        invalid_pass=bool(report and report['outcome']=='pass' and (report.get('audible') is not True or report.get('response_correct') is not True))
        if plan['kind']=='interrupt' and report and report['outcome']=='pass' and not all(report.get(k) is True for k in ['overlap','old_stopped','no_replay']):invalid_pass=True
        if state=='pass' and (errors or early or invalid_pass or plan['kind']=='task' and not (task and task.get('verified'))):state='fail'
        primary=seconds(endpoint,audio);whole=seconds(onset,audio)
        if primary is not None and primary<0:primary=None
        asr=[x for x in stage if x['role']=='asr'];last_tts=next((x for x in reversed(stage) if x['role']=='tts' and x['generation']==g and (audio is None or x['start_ns']<=ns(audio))),None);last_reason=next((x for x in reversed(stage) if x['role']=='reasoning' and x['generation']==g),None)
        entries.append({'trial_id':trial,'kind':plan['kind'],'case':plan.get('case'),'state':state,'operator':report,'started':start is not None,'audio_observed':audio is not None,'endpoint_to_substantive_submission_seconds':primary,'onset_to_substantive_submission_seconds':whole,'ASR_attempts':len(asr),'single_ASR_segment':len(asr)==1,'selected_ASR_seconds':selected_asr['complete_seconds'] if selected_asr else None,'reasoning_seconds':last_reason['complete_seconds'] if last_reason else None,'TTS_seconds':last_tts['complete_seconds'] if last_tts else None,'interruption_detected':overlap is not None,'interruption_takeover':bool(overlap and audio and ns(audio)>=ns(overlap)),'interruption_onset_to_cancel_seconds':seconds(overlap,cancel),'interruption_onset_to_callback_stop_proxy_seconds':seconds(overlap,stop_proxy),'interruption_onset_to_replacement_submission_seconds':seconds(overlap,audio),'stop_method':'software callback silence after explicit suspension; NOT physical acoustic stop','premature_substantive_takeover':early,'invalid_operator_pass':invalid_pass,'stages':stage,'timeout':any(x['state']=='timeout' for x in stage) or any(x['event']=='core_turn_deadline' for x in own),'task_start_to_independent_verification_seconds':(task['observed_ns']-ns(onset))/1e9 if plan['kind']=='task' and task and onset else None})
    normal=[x for x in entries if x['kind']=='normal'];warm=[x for x in entries if x['kind']=='warmup'];split=[x for x in entries if x['kind'] in {'split_complete','split_replace'}];interrupt=[x for x in entries if x['kind']=='interrupt'];counts=collections.Counter(x['state'] for x in entries);n=len(entries);started=sum(x['started'] for x in entries)
    cues=[x for x in data if x['event']=='completion_agent_backchannel'];floor_seconds=0
    for t in entries:
        own=[x for x in data if x.get('trial_id')==t['trial_id']];a=next((x for x in own if x['event']=='bf_speech_onset'),None);b=next((x for x in reversed(own) if x['event']=='bf_endpoint'),None)
        if a and b:floor_seconds+=max(0,seconds(a,b))
    startup=stages([x for x in data if x.get('trial_id') is None])
    started_interrupt=[x for x in interrupt if x['started']]
    takeover_interrupt=[x for x in started_interrupt if x['interruption_takeover']]
    return {'startup_requests':{'scope':'trial-unattributed startup requests, cue priming and greeting may share the resident worker; not a matched M6 cold-start comparison','stages':startup,'TTS':stats([x['complete_seconds'] for x in startup if x['role']=='tts'])},'interruption_summary':{'started':len(started_interrupt),'detected':sum(x['interruption_detected'] for x in started_interrupt),'takeovers':len(takeover_interrupt),'takeover_rate_of_started':len(takeover_interrupt)/len(started_interrupt) if started_interrupt else None,'method':'TO=1 if replacement substantive output callback follows a detected overlap; software proxy plus separate operator corroboration, not physical timing','latency_only_TO1':stats([x['interruption_onset_to_replacement_submission_seconds'] for x in takeover_interrupt]),'operator_correct_only_TO1':sum(x['state']=='pass' for x in takeover_interrupt)},'analysis_version':VERSION,'accounting':{'planned':n,'started':started,'outcomes':dict(counts),'failure_rate_of_started':sum(x['state'] in {'fail','timeout'} for x in entries)/started if started else None,'timeout_trials':sum(x['timeout'] for x in entries),'incomplete_trials':counts['incomplete'],'unstarted_trials':counts['unstarted'],'ASR_cancellations':sum(x['role']=='asr' and x['state']=='canceled' for x in all_stages)},'trials':entries,'normal_latency':{'scope':'all available normal outcomes including semantic failures; no silently filtered error/failed cohort','endpoint_to_submission':stats([x['endpoint_to_substantive_submission_seconds'] for x in normal]),'onset_to_submission':stats([x['onset_to_substantive_submission_seconds'] for x in normal]),'ASR_last_attempt':stats([x['selected_ASR_seconds'] for x in normal]),'ASR_all_completed_normal_requests':stats([x['complete_seconds'] for x in all_stages if x['role']=='asr' and x['kind']=='normal']),'ASR_single_segment':stats([x['selected_ASR_seconds'] for x in normal if x['single_ASR_segment']]),'reasoning':stats([x['reasoning_seconds'] for x in normal]),'TTS':stats([x['TTS_seconds'] for x in normal]),'warm_single_ASR_segment_endpoint':stats([x['endpoint_to_substantive_submission_seconds'] for x in normal if x['single_ASR_segment']])},'warmup_latency':stats([x['endpoint_to_substantive_submission_seconds'] for x in warm]),'split_recovery':{'planned':len(split),'started':sum(x['started'] for x in split),'passed':sum(x['state']=='pass' for x in split),'success_rate_of_started':sum(x['state']=='pass' for x in split)/sum(x['started'] for x in split) if any(x['started'] for x in split) else None,'premature_takeover_rate':sum(x['premature_substantive_takeover'] for x in split)/sum(x['started'] for x in split) if any(x['started'] for x in split) else None,'method':'substantive callback before final scripted chunk endpoint; software proxy, operator reports separate'},'interruptions':interrupt,'backchannels':{'events':len(cues),'events_per_minute_of_user_floor_proxy':len(cues)/(floor_seconds/60) if floor_seconds else None,'user_floor_proxy_seconds':floor_seconds,'method':'sum first VAD onset to final VAD endpoint per started trial; contains intra-turn silence, excludes UI idle gaps','JSD':{'status':'NOT_MEASURED','reason':'No aligned human reference timing; never substitute zero'}},'GPT4o_quality':{'status':'NOT_MEASURED','reason':'No authorized GPT-4o evaluation results; operator correctness is not a GPT score'},'physical_acoustic_onset_stop':'NOT_MEASURED','task_independent_verification':task,'all_stages':all_stages,'incidentals':[x for x in data if x['event']=='m6_incidental_trial'],'limits':['Same-Pi monotonic software boundaries only; human audibility is categorical.','Twenty normal scripts compare with M6; changed task case is not a matched task speedup.','Cold startup and warm-up are separate; p95 absent below20available observations.']}
def analyze(folder):
    folder=Path(folder);manifest=read(folder/'manifest.json');trials={t['id']:{**t,'session':s} for s,items in manifest['protocol'].items() for t in items};data=[];hashes={}
    for s in manifest['protocol']:
        for suffix in ['events.jsonl','operator-late.jsonl']:
            path=folder/s/suffix;data.extend(rows(path))
            if path.exists():hashes[path.relative_to(folder).as_posix()]=hashlib.sha256(path.read_bytes()).hexdigest()
    data.sort(key=ns);task_path=folder/'task-independent-verification.json';result=analyze_data(trials,data,read(task_path) if task_path.exists() else None);result.update(request_budget=read(folder/'budget.json') if (folder/'budget.json').exists() else None,session_exits={s:read(folder/s/'process-exit.json') if (folder/s/'process-exit.json').exists() else None for s in manifest['protocol']},source_revision=manifest['source']['revision'],harness_revision=manifest['harness']['revision'],input_sha256=hashes,environment=manifest['environment'],methods=manifest['methods'])
    return result
