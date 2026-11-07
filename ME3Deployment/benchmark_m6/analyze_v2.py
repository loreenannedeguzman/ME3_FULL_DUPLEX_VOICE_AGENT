"""Post-baseline accounting correction; original observer, reducer and raw logs stay immutable."""
import collections,hashlib,json
from pathlib import Path
from benchmark_m6.analyze import analyze as original_analyze,stamp,duration,stats
from benchmark_m6.protocol import TRIALS
from benchmark_m6.storage import read,rows
VERSION='m6-analysis-v2'

def terminal_stage(data,start,end_event,cancel_event,error_event,request_id=None):
    if start is None:return {'state':'not_started','complete_ms':None,'elapsed_to_terminal_ms':None,'censored':False}
    candidates=[r for r in data if r['event'] in {end_event,cancel_event,error_event} and stamp(r)>=stamp(start) and (request_id is None or r.get('request_id')==request_id)]
    terminal=min(candidates,key=stamp) if candidates else None
    state='incomplete' if terminal is None else 'complete' if terminal['event']==end_event else 'canceled' if terminal['event']==cancel_event else 'timeout' if terminal.get('error_type') in {'TimeoutError','TimeoutException','ReadTimeout','ConnectTimeout'} else 'error'
    elapsed=duration(start,terminal)
    return {'state':state,'complete_ms':elapsed if state=='complete' else None,'elapsed_to_terminal_ms':elapsed,'censored':state in {'canceled','timeout','error','incomplete'}}

def is_timeout(row):
    return row['event']=='m6_trial_timeout' or (row['event'] in {'mcp_request_error','turn_error','m6_reasoning_request_error'} and row.get('error_type') in {'TimeoutError','TimeoutException','ReadTimeout','ConnectTimeout'})

def analyze(folder):
    folder=Path(folder);result=original_analyze(folder);data=[]
    for session in 'ABCDEF':
        for row in rows(folder/session/'events.jsonl'):
            row.setdefault('session_id',session);data.append(row)
    for attempt in result['attempts']:
        own=[r for r in data if r['session_id']==attempt['session'] and r.get('generation')==attempt['generation']]
        stages={}
        for role in ('asr','tts'):
            start=next((r for r in own if r['event']=='mcp_request_start' and r['role']==role),None)
            stages[role]=terminal_stage(own,start,'mcp_request_end','mcp_request_cancelled','mcp_request_error',start['request_id'] if start else None)
        start=next((r for r in own if r['event']=='m6_reasoning_request_start'),None)
        stages['reasoning']=terminal_stage(own,start,'m6_reasoning_request_end','m6_reasoning_request_cancelled','m6_reasoning_request_error')
        attempt['stages']=stages
        # A failed/canceled stage's elapsed time is censored, never a completed duration.
        for role in ('asr','tts'):attempt[role+'_ms']=stages[role]['complete_ms']
        attempt['reasoning_request_ms']=stages['reasoning']['complete_ms']
    for trial in result['trials']:
        own=[r for r in data if r.get('trial_id')==trial['trial_id']]
        timeout_sources=[{'event':r['event'],'role':r.get('role',r.get('stage')),'error_type':r.get('error_type'),'observed_ns':stamp(r)} for r in own if is_timeout(r)]
        trial['timeout']=bool(timeout_sources);trial['timeout_sources']=timeout_sources
        if timeout_sources and trial['outcome']!='unstarted':trial['outcome']='timeout'
        elif trial['outcome']=='incomplete' and trial['errors']:trial['outcome']='fail'
        trial['session_stop_reason']=next((r.get('reason') for r in own if r['event']=='session_stop'),None)
        if trial['kind']=='task':
            verification=next((r for r in own if r['event']=='m6_task_external_verification'),None)
            clock=next((r for r in data if r['session_id']==trial['session'] and r['event']=='m6_clock'),None)
            same_boot=bool(verification and clock and verification.get('verifier_boot_id')==clock.get('boot_id') and clock.get('boot_id'))
            trial['checks']['independent_verifier_same_boot']=same_boot
            if verification and not same_boot:
                for key in ('speech_onset_proxy_to_independent_verification_ms','commit_to_independent_verification_ms'):trial['task_timing'][key]=None
                if trial['outcome']=='pass':trial['outcome']='fail'
        if trial['outcome']=='pass' and not all(trial['checks'].values()):trial['outcome']='fail'
    counts=collections.Counter(t['outcome'] for t in result['trials']);started=[t for t in result['trials'] if t['outcome']!='unstarted'];failed=sum(counts[k] for k in ('fail','timeout','incomplete'))
    accounting=result['trial_accounting'];accounting.update(outcomes=dict(counts),failure_timeout_incomplete_rate_of_started=failed/len(started) if started else None,timeout_rate_of_started=counts['timeout']/len(started) if started else None,incomplete_rate_of_started=counts['incomplete']/len(started) if started else None)
    accounting['nonpass_or_unstarted_rate_of_planned']=(failed+counts['unstarted'])/len(TRIALS)
    accounting['failed_semantic_or_structural_rate_of_started']=counts['fail']/len(started) if started else None
    for kind,item in result['accounting_by_kind'].items():
        subset=[t for t in result['trials'] if t['kind']==kind];active=[t for t in subset if t['outcome']!='unstarted'];item['outcomes']=dict(collections.Counter(t['outcome'] for t in subset));item['failure_rate_of_started']=sum(t['outcome'] in {'fail','timeout','incomplete'} for t in active)/len(active) if active else None
    success_ids={t['trial_id'] for t in result['trials'] if t['outcome']=='pass'}
    for name,success_only in [('steady_normal_all_available_including_semantic_failures',False),('steady_normal_successful_only',True)]:
        eligible_ids={t['trial_id'] for t in result['trials'] if t['kind']=='normal' and t['attempt_count']==1 and (not success_only or t['trial_id'] in success_ids)}
        eligible=[a for a in result['attempts'] if a['trial_id'] in eligible_ids and a['tts_cold_start'] is False]
        result[name]={field:stats([a[field] for a in eligible]) for field in result[name]}
    split=[t for t in started if t['kind'].startswith('split_')];result['split_recovery']={'started':len(split),'successes':sum(t['outcome']=='pass' for t in split),'success_rate':sum(t['outcome']=='pass' for t in split)/len(split) if split else None}
    stage_accounting={}
    for kind in sorted({t['kind'] for t in result['trials']}):
        ids={t['trial_id'] for t in result['trials'] if t['kind']==kind};attempts=[a for a in result['attempts'] if a['trial_id'] in ids];stage_accounting[kind]={}
        for role in ('asr','reasoning','tts'):
            stage=[a['stages'][role] for a in attempts];stage_accounting[kind][role]={'attempt_generations':len(stage),'terminal_states':dict(collections.Counter(s['state'] for s in stage)),'completed_duration':stats([s['complete_ms'] for s in stage]),'censored_attempts':[{'trial_id':a['trial_id'],'generation':a['generation'],**a['stages'][role]} for a in attempts if a['stages'][role]['censored']]}
    result['stage_accounting_including_segmentation_and_failed_attempts']=stage_accounting
    # Keep all available ordinary-attempt timings as a separate table, not a hidden replacement for the preregistered single-utterance stratum.
    normal_ids={t['trial_id'] for t in result['trials'] if t['kind']=='normal'};normal=[a for a in result['attempts'] if a['trial_id'] in normal_ids and a['tts_cold_start'] is False]
    result['all_normal_warm_attempts_including_segmentation_failures']={field:stats([a[field] for a in normal]) for field in result['steady_normal_all_available_including_semantic_failures']}
    budget_path=folder/'budget.json';actual={'asr':sum(r['event']=='mcp_request_start' and r.get('role')=='asr' for r in data),'tts':sum(r['event']=='mcp_request_start' and r.get('role')=='tts' for r in data),'reasoning':sum(r['event']=='m6_reasoning_request_start' for r in data),'commit_item':sum(r['event']=='task_mcp_request_start' and r.get('tool')=='commit_item' for r in data)}
    result['request_inventory']={'observed_starts':actual,'reserved_budget':read(budget_path) if budget_path.exists() else None,'note':'Reservations are conservative; canceled requests may still consume provider allowance.'}
    source=folder/'manifest.json';manifest=read(source) if source.exists() else {}
    supplement=folder/'operator-supplement.json';result['operator_supplement']=read(supplement) if supplement.exists() else None
    result['analysis_provenance']={'version':VERSION,'live_source_revision':manifest.get('source',{}).get('revision'),'original_reducer_sha256':hashlib.sha256(Path(__file__).with_name('analyze.py').read_bytes()).hexdigest(),'corrected_reducer_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'operator_supplement_sha256':hashlib.sha256(supplement.read_bytes()).hexdigest() if supplement.exists() else None,'correction':'Recognize provider TimeoutError from existing raw events, retain censored stage attempts, verify same-boot independent task clock. No raw events, criteria or runtime files changed.'}
    result['limitations'].extend(['Ordinary primary latency stratum requires one detected segment and observed warm TTS; missing/segmented attempts remain in ledger and stage-accounting tables. It is not an unconditional latency for every request.','An extra response preceded C02; no retained waveform/transcript can determine the cause of extra segmentation or unexpected language.'])
    assert accounting['total']==sum(counts.values()) and accounting['started']==len(started)
    return result
