"""Reuse tested callback/trial reducer, add real model-step and task-RPC accounting."""
from pathlib import Path
from benchmark_agent.interaction_metrics import interaction_metrics
from benchmark_final.analyze import analyze as base_analyze,ns,stats
from benchmark_m6.storage import rows,read
def analyze(folder):
    folder=Path(folder);out=base_analyze(folder);manifest=read(folder/'manifest.json');events=[]
    for session in manifest['protocol']:events+=rows(folder/session/'events.jsonl')
    gateway={r.get('request_id') for r in events if r['event']=='mcp_request_error' and r.get('role')=='asr' and r.get('code')==504}
    for trial in out['trials']:
        if any(s.get('request_id') in gateway for s in trial['stages']):trial['timeout']=True
    for stage in out['all_stages']+[s for t in out['trials'] for s in t['stages']]:
        if stage.get('request_id') in gateway:stage.update(state='timeout',upstream_status=504)
    out['accounting']['timeout_trials']=sum(t['timeout'] for t in out['trials'])
    starts={};requests=[]
    for row in sorted(events,key=ns):
        event=row['event'];key=(row.get('session_id'),row.get('request_id',row.get('step_id')))
        if event in {'ba_llm_start','task_mcp_request_start'}:starts[key]=row
        elif event in {'ba_llm_end','ba_llm_cancelled','ba_llm_error','task_mcp_request_end','task_mcp_request_cancelled','task_mcp_request_error'}:
            start=starts.pop(key,None)
            if not start:continue
            state='complete' if event in {'ba_llm_end','task_mcp_request_end'} else 'canceled' if event.endswith('cancelled') else 'error'
            requests.append({'kind':'llm' if start['event']=='ba_llm_start' else 'task_rpc','trial_id':start.get('trial_id'),'generation':start.get('generation'),'tool':start.get('tool'),'state':state,'seconds':(ns(row)-ns(start))/1e9,'censored':state!='complete'})
    for start in starts.values():requests.append({'kind':'llm' if start['event']=='ba_llm_start' else 'task_rpc','trial_id':start.get('trial_id'),'state':'incomplete','seconds':None,'censored':True})
    out.update(agent_analysis_version='agent-v2',agent_model_and_tool_requests=requests,LLM_complete_request=stats([r['seconds'] for r in requests if r['kind']=='llm' and r['state']=='complete']),task_RPC_complete_request=stats([r['seconds'] for r in requests if r['kind']=='task_rpc' and r['state']=='complete']),LLM_TTFT={'status':'NOT_MEASURED','reason':'Nonstreaming API; full request duration is separate.'})
    out['limits'].append('F02 is a read-only list query, not a matched write benchmark; no task speedup claim. Planning loop includes real MCP tools; separate LLM requests are provided.')
    plans={t['id']:t for items in manifest['protocol'].values() for t in items}
    out.update(interaction_metrics(plans,out['trials'],events))
    return out
