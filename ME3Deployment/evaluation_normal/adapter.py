"""Observational normal-assistant benchmark adapter. No allowance creation or retries.

Run authorization must be supplied separately. This module never initializes,
resets, transfers, or enlarges a usage counter. The 210-file qualified runtime
remains unchanged; this adapter has its own separately hashed source identity.
"""
import argparse,asyncio,copy,hashlib,json,logging,sqlite3,sys,time
from contextlib import closing
from contextlib import contextmanager
from pathlib import Path

MAX_NEW={'asr':48,'reasoning':60,'tts':64,'commit_item':0}
PRIVATE={'text','content','transcript','prompt','arguments','result','messages','pcm_b64','audio','api_key'}

def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def project_path(root,path):
    p=Path(path).resolve();p.relative_to(Path(root).resolve()/'evidence'/'benchmark_normal');return p

def protocol():
    from benchmark_agent.protocol import SESSIONS,METHODS
    plan=copy.deepcopy(SESSIONS);methods=copy.deepcopy(METHODS)
    plan['F'][-1]['expected']='Actual shopping list_items MCP; independent read-only SQL verifies the unchanged seven rows, including one paperclip; complete spoken read-back. No proposal or write.'
    methods.update(reasoning='Whole actual normal Router.answer interval, including dynamically selected tools; separate actual normal Reasoning.decide request intervals.',task='Read-only seven-row shopping list; not matched to older write or six-row trials.',runtime='Qualified normal me3_assistant.runner.run, Router, AssistantController and registry; only observational wrappers and explicitly scoped benchmark budget dependencies.',budget='Separate explicit benchmark authorization; cumulative opening balance links immutable prior benchmark usage. No development allowance or old-run reset.',physical_acoustic_onset_stop='NOT_MEASURED without calibrated capture',JSD='NOT_MEASURED without genuine aligned human timing reference',GPT4o_quality='NOT_MEASURED without authorized evaluator/results')
    assert sum(map(len,plan.values()))==31
    return plan,methods

class EventBridge:
    def __init__(self,events,task_verify=None):self.events=events;self.task_verify=task_verify
    def __call__(self,event,**fields):
        safe={k:v for k,v in fields.items() if k not in PRIVATE}
        self.events.emit(event,**safe)
        mapping={'assistant_budget_reserved':'m6_budget_reserved','reasoning_start':'m6_reasoning_request_start','reasoning_end':'m6_reasoning_request_end','assistant_rpc_start':'task_mcp_request_start','assistant_rpc_end':'task_mcp_request_end','assistant_rpc_error':'task_mcp_request_error','assistant_rpc_canceled':'task_mcp_request_cancelled'}
        alias=mapping.get(event)
        if event=='turn_task_cancelled' and fields.get('stage')=='reasoning':alias='m6_reasoning_request_cancelled'
        if event=='turn_error' and fields.get('stage')=='reasoning':alias='m6_reasoning_request_error'
        if alias:self.events.emit(alias,**safe)
        if event=='assistant_rpc_end' and fields.get('server')=='shopping' and fields.get('tool')=='list_items' and self.events.active=='F02' and self.task_verify:
            self.events.emit('m6_task_external_verification',trial_id='F02',**self.task_verify())

def verify_task(root,expected_hash):
    db=Path(root)/'data/shopping.sqlite3'
    with closing(sqlite3.connect(db.as_uri()+'?mode=ro',uri=True)) as c:
        count=c.execute('SELECT count(*) FROM items').fetchone()[0]
    if count!=7 or sha(db)!=expected_hash:raise ValueError('benchmark_task_state_changed')
    return {'verified':True,'row_count':count,'database_sha256':expected_hash,'no_write':True,'observed_ns':time.monotonic_ns(),'verifier_boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),'method':'Independent read-only SQLite outside task MCP plus exact frozen DB hash; actual list_items RPC required.'}

@contextmanager
def bind_runtime(module,phase,grant_path,events,attach):
    """Bind the original guard to an explicit benchmark scope, never bypass it.

    The guard checks the same source/config/freshness/module/schema/expiry rules.
    Only budget metadata/paths differ. No models, prompts, VAD, turn rules,
    speech splitting, tool routing, cancellation or retry behavior are changed.
    """
    old_auth,old_budget,old_observe,old_reasoning=module.authorize,module.SharedBudget,module.observe_pipeline,module.Reasoning
    class ObservedReasoning(old_reasoning):
        async def decide(self,*args):
            index=getattr(self,'benchmark_step',0)+1;self.benchmark_step=index
            fields={'generation':events.current_generation,'step_id':str(index),'observed_ns':time.monotonic_ns()}
            events.emit('ba_llm_start',**fields)
            try:
                result=await super().decide(*args)
                events.emit('ba_llm_end',**{**fields,'observed_ns':time.monotonic_ns()});return result
            except asyncio.CancelledError:
                events.emit('ba_llm_cancelled',**{**fields,'observed_ns':time.monotonic_ns()});raise
            except Exception:
                events.emit('ba_llm_error',**{**fields,'observed_ns':time.monotonic_ns()});raise
    def scoped_auth(config,source):
        scoped=dict(source);scoped['budget_limits']=read(Path(phase)/'authorization.json')['limits']
        grant,limits=old_auth(config,scoped,grant_path=grant_path,phase=phase)
        if grant.get('allowed_actions')!=[] or limits['commit_item']!=0:raise ValueError('benchmark_must_be_readonly')
        return grant,limits
    def scoped_budget(grant,limits):return old_budget(grant,limits,phase=phase)
    def observe(result,settings,normal_events):
        old_observe(result,settings,normal_events)
        events.processor=result[3];attach(result[3],result[2],events)
    module.authorize=scoped_auth;module.SharedBudget=scoped_budget;module.observe_pipeline=observe;module.Reasoning=ObservedReasoning
    try:yield
    finally:module.authorize=old_auth;module.SharedBudget=old_budget;module.observe_pipeline=old_observe;module.Reasoning=old_reasoning

def verify_history(root,phase,authority):
    """Require immutable, monotonic links back to the original benchmark counter."""
    root=Path(root).resolve();phase=Path(phase).resolve()
    history=authority['historical_benchmark_baseline'];chain=authority.get('historical_benchmark_chain',[history])
    if not isinstance(chain,list) or not 1<=len(chain)<=16 or chain[-1]!=history:raise ValueError('explicit_benchmark_history_required')
    original=root/'evidence/benchmark_agent/run-20261021-001/budget.json';previous=None;seen=set()
    for i,record in enumerate(chain):
        p=(root/record['path']).resolve()
        if i==0:
            if p!=original:raise ValueError('original_benchmark_anchor_required')
        else:
            project_path(root,p)
            if p.name!='budget.json' or not p.parent.name.startswith('accounting-'):raise ValueError('historical_benchmark_scope_required')
        if p==phase/'budget.json' or p in seen:raise ValueError('immutable_previous_scope_required')
        seen.add(p);usage=record['usage']
        if set(usage)!=set(MAX_NEW) or any(type(v) is not int or v<0 for v in usage.values()):raise ValueError('invalid_historical_usage')
        if sha(p)!=record['sha256'] or read(p)!=usage:raise ValueError('historical_benchmark_counter_changed')
        if previous is not None and any(usage[k]<previous[k] for k in MAX_NEW):raise ValueError('historical_usage_must_be_monotonic')
        previous=usage
    return history

def verify_run(root,run_folder,session,phase,grant_path):
    """Fail before opening audio, providers or event files if anything is absent."""
    root=Path(root);run_folder=project_path(root,run_folder);phase=project_path(root,phase);grant_path=project_path(root,grant_path)
    manifest=read(run_folder/'manifest.json');authority=read(phase/'authorization.json');grant=read(grant_path);usage=read(phase/'budget.json')
    from me3_assistant.config import load
    from me3_assistant.guard import verify_sources,authorize
    freeze=root/'evidence/assistant/qualified-20261022-003';receipt=read(freeze/'receipt.json');source=verify_sources(root)
    assert sha(freeze/'receipt.json')=='79c47124d567f5394c5f39380a0bea07d6259732f6f6342dcbcdc9365db0dfd1'
    if receipt['functional_gate']!='PASS_WITH_DISCLOSED_EVIDENCE_SCOPE' or source['revision']!=receipt['source_revision']:raise ValueError('qualified_candidate_required')
    plan,methods=protocol()
    if session not in plan or manifest['protocol']!=plan or manifest['methods']!=methods:raise ValueError('fixed31trial_protocol_required')
    if manifest['source_revision']!=source['revision'] or manifest['adapter_sha256']!=sha(__file__):raise ValueError('source_or_harness_changed')
    if manifest['budget_scope']!=str(phase) or manifest['grant_paths'].get(session)!=str(grant_path):raise ValueError('benchmark_scope_not_bound')
    if Path('/proc/sys/kernel/random/boot_id').read_text().strip()!=manifest['boot_id']:raise ValueError('benchmark_clock_boot_changed')
    if (run_folder/session).exists():raise ValueError('session_already_attempted_no_retry')
    if authority.get('budget_scope')!='benchmark_normal' or authority.get('no_additional_writes') is not True:raise ValueError('explicit_benchmark_authority_required')
    history=verify_history(root,phase,authority)
    start=authority['aggregate_start'];new=authority['new_reservation_ceiling'];limits=authority['limits']
    if set(start)!=set(MAX_NEW) or set(new)!=set(MAX_NEW) or set(limits)!=set(MAX_NEW) or set(usage)!=set(MAX_NEW):raise ValueError('benchmark_budget_keys_required')
    for k in MAX_NEW:
        if any(type(v) is not int for v in (start[k],new[k],limits[k],usage[k])) or not 0<=new[k]<=MAX_NEW[k] or start[k]!=history['usage'][k] or not start[k]<=usage[k]<=limits[k] or limits[k]!=start[k]+new[k]:raise ValueError('explicit_monotonic_benchmark_allocation_required')
    if grant['budget_start']!=start or grant['max_reservations']!=new or new['commit_item'] or grant.get('allowed_actions')!=[]:raise ValueError('readonly_grant_must_match_explicit_allocation')
    # A fresh complete31trial run cannot be launched under13 remaining ASR calls.
    # This deliberately conservative lower bound is not a call-cost prediction.
    if new['asr']<31:raise ValueError('allowance_cannot_cover31planned_spoken_trials')
    if manifest['task_database_sha256']!=receipt['database_sha256']:raise ValueError('frozen_task_baseline_required')
    verify_task(root,manifest['task_database_sha256'])
    scoped=dict(source);scoped['budget_limits']=limits;authorize(load(root/'config/assistant.json'),scoped,grant_path=grant_path,phase=phase)
    return manifest

async def run(root,run_folder,session,phase,grant_path):
    manifest=verify_run(root,run_folder,session,phase,grant_path)
    if sys.platform!='linux':raise ValueError('Pi_required')
    # Import/construct runtime only after all explicit allowance/source checks.
    from me3_assistant import runner as normal
    from me3_assistant.config import load
    from benchmark_final.runner import Events,commands
    from benchmark_final.observe import attach
    events=Events(Path(run_folder)/session,session);watcher=None
    def task_verification():
        from benchmark_m6.storage import write_new
        result=verify_task(root,manifest['task_database_sha256'])
        path=Path(run_folder)/'task-independent-verification.json'
        if not path.exists():write_new(path,result)
        elif read(path)['database_sha256']!=result['database_sha256']:raise ValueError('independent_task_verification_changed')
        return result
    try:
        events.emit('normal_benchmark_configuration',source_revision=manifest['source_revision'],adapter_sha256=manifest['adapter_sha256'],budget_scope=str(phase),task_write_budget=0,controlled_faults=False,acoustic=False)
        with bind_runtime(normal,phase,grant_path,events,attach):
            watcher=asyncio.create_task(commands(events,Path(run_folder)/session))
            await normal.run(load(Path(root)/'config/assistant.json'),observer=EventBridge(events,task_verification))
            verify_task(root,manifest['task_database_sha256'])
    finally:
        if watcher:watcher.cancel();await asyncio.gather(watcher,return_exceptions=True)
        if events.active is not None:events.emit('m6_trial_unfinished',trial_id=events.active)
        events.emit('normal_benchmark_session_finished');events.close()

def preflight(root):
    root=Path(root);plan,_=protocol();old=read(root/'evidence/benchmark_agent/run-20261021-001/budget.json')
    remaining={k:MAX_NEW[k]-old[k] for k in MAX_NEW}
    return {'planned_trials':sum(map(len,plan.values())),'runtime':'me3_assistant.runner.run (normal Router/AssistantController)','current_benchmark_usage':old,'remaining_old_allocation':remaining,'full31trial_run_possible_under_old_remaining_ASR':remaining['asr']>=31,'status':'BLOCKED_PENDING_EXPLICIT_NEW_RUN_MANIFEST_AND_ALLOWANCE','counters_written':False,'provider_calls':0,'tts_calls':0,'task_writes':0,'raw_audio_or_transcripts_saved':False}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');p.add_argument('--run',type=Path);p.add_argument('--session');p.add_argument('--scope',type=Path);p.add_argument('--grant',type=Path);a=p.parse_args()
    from task_mcp.client import ROOT
    if a.check:print(json.dumps(preflight(ROOT)));return
    if not all((a.run,a.session,a.scope,a.grant)):p.error('--run --session --scope --grant required; no automatic authorization or budget initialization')
    from loguru import logger
    logger.remove();logging.disable(logging.CRITICAL)
    try:asyncio.run(run(ROOT,a.run,a.session,a.scope,a.grant));return 0
    except KeyboardInterrupt:return 130
    except Exception as exc:
        # Provider exceptions can embed payloads. Never render their message/body.
        print(json.dumps({'status':'FAIL','error_type':type(exc).__name__,'payload_omitted':True}),file=sys.stderr);return 2
if __name__=='__main__':raise SystemExit(main())
