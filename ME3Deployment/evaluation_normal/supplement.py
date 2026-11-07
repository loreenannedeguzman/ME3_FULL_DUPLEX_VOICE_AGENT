"""Two separately labelled, previously unstarted benchmark scenarios only.

Original 31-trial ledger, failed F00, execution hold and budget stay immutable.
No original trial is retried or relabelled. No authority/counter is created here.
"""
import argparse,asyncio,hashlib,json,logging,sys
from pathlib import Path
from evaluation_normal.adapter import (read,sha,project_path,protocol,verify_history,
                                      verify_task,bind_runtime,EventBridge)

SELECTED=['F01','F02']
SOURCE='bfe79de875d33516024158467cbcff260d3263d273793e6c7a328fee371bd8a7'
FREEZE_SHA='79c47124d567f5394c5f39380a0bea07d6259732f6f6342dcbcdc9365db0dfd1'

def validate_parent(manifest,ledger,receipt,hold,selected):
    plan,methods=protocol()
    if selected!=SELECTED or manifest['protocol']!=plan or manifest['methods']!=methods:
        raise ValueError('only_exact_unstarted_F01_F02_protocol')
    if manifest['source_revision']!=SOURCE or receipt['source_revision']!=SOURCE:
        raise ValueError('same_frozen_candidate_required')
    if not ledger.get('continuation_prohibited') or hold.get('no_further_calls') is not True:
        raise ValueError('original_failure_and_hold_must_remain')
    trials=ledger['trials']
    expected={t['id'] for ts in plan.values() for t in ts}
    if len(trials)!=31 or {t['id'] for t in trials}!=expected or {t['id'] for t in trials if t['status']=='UNSTARTED'}!=set(SELECTED):
        raise ValueError('parent31identities_and_unstarted_required')
    if next(t for t in trials if t['id']=='F00')['status']!='FAIL' or receipt['operator_failed']!=1:
        raise ValueError('original_F00_failure_required')

def validate_allocation(authority,parent_authority,parent_usage):
    parent_limits=parent_authority['limits'];start=authority['aggregate_start'];cap=authority['new_reservation_ceiling']
    extra=authority.get('authorization_increment',{k:0 for k in parent_limits})
    if set(extra)!=set(parent_limits) or any(type(v) is not int or v<0 for v in extra.values()) or extra['commit_item']:
        raise ValueError('explicit_readonly_cap_amendment_required')
    limits={k:parent_limits[k]+extra[k] for k in parent_limits}
    maximum={'asr':3,'reasoning':3,'tts':21,'commit_item':0}
    if (authority.get('approved') is not True or authority.get('budget_scope')!='benchmark_normal' or
        authority.get('no_additional_writes') is not True or start!=parent_usage or authority['limits']!=limits or
        set(cap)!=set(limits) or any(type(cap[k]) is not int or cap[k]!=limits[k]-start[k] or cap[k]>maximum[k] for k in limits) or
        limits['commit_item'] or cap['asr']<3 or cap['reasoning']<3 or cap['tts']<6):
        raise ValueError('only_unchanged_held_benchmark_allocation')

def verify(root,run_folder,phase,grant_path):
    root=Path(root);run_folder=project_path(root,run_folder);phase=project_path(root,phase);grant_path=project_path(root,grant_path)
    m=read(run_folder/'manifest.json');parent=project_path(root,root/m['parent_run']);oldphase=project_path(root,root/m['parent_scope'])
    if parent==run_folder or phase==oldphase:raise ValueError('supplement_must_be_separate')
    for name,value in m['immutable_parent_files'].items():
        p=project_path(root,root/name)
        if sha(p)!=value:raise ValueError('original_evidence_changed')
    validate_parent(read(parent/'manifest.json'),read(parent/'trial-ledger.json'),read(parent/'receipt.json'),read(oldphase/'EXECUTION_HOLD.json'),m['selected_trial_ids'])
    if sha(__file__)!=m['supplement_sha256'] or sha(Path(__file__).with_name('adapter.py'))!=m['adapter_sha256']:
        raise ValueError('supplement_harness_changed')
    from me3_assistant.guard import verify_sources,authorize
    from me3_assistant.config import load
    source=verify_sources(root);freeze=root/'evidence/assistant/qualified-20261022-003/receipt.json'
    qual=root/'evidence/assistant/reliability-20261022-001/receipt.json'
    if source['revision']!=SOURCE or sha(freeze)!=FREEZE_SHA or sha(qual)!=m['qualification_sha256'] or read(qual)['result']!='PASS_WITH_DISCLOSED_EVIDENCE_SCOPE':
        raise ValueError('current_finite_functional_qualification_required')
    if (run_folder/'F').exists():raise ValueError('supplement_already_attempted_no_retry')
    if Path('/proc/sys/kernel/random/boot_id').read_text().strip()!=m['boot_id']:raise ValueError('supplement_boot_changed')
    if read(root/'evidence/completion/autonomous-20261020-001/budget.json')!=m['development_usage_unchanged']:raise ValueError('development_counter_changed')
    authority=read(phase/'authorization.json');oldauth=read(oldphase/'authorization.json');oldusage=read(oldphase/'budget.json')
    validate_allocation(authority,oldauth,oldusage);verify_history(root,phase,authority)
    if authority['historical_benchmark_baseline']['usage']!=oldusage or (root/authority['historical_benchmark_baseline']['path']).resolve()!=oldphase/'budget.json':
        raise ValueError('explicit_parent_counter_anchor_required')
    if str(phase)!=m['budget_scope'] or str(grant_path)!=m['grant_path']:raise ValueError('supplement_grant_scope_not_bound')
    # Delegate freshness, schema/module, allowance, readiness and remaining-cap checks.
    scoped=dict(source,budget_limits=authority['limits']);grant,limits=authorize(load(root/'config/assistant.json'),scoped,grant_path=grant_path,phase=phase)
    if grant.get('allowed_actions')!=[] or grant['budget_start']!=oldusage or grant['max_reservations']!=authority['new_reservation_ceiling']:
        raise ValueError('explicit_readonly_supplement_grant_required')
    verify_task(root,m['task_database_sha256'])
    return m

class SelectionGuard:
    def __init__(self,*a):super().__init__(*a);self.finished=[];self.failed=False
    def command(self,c):
        if c['action']=='cue':
            if self.failed or self.active is not None or self.finished!=SELECTED[:len(self.finished)] or len(self.finished)>=2 or c['trial']!=SELECTED[len(self.finished)]:
                raise ValueError('only_ordered_unstarted_supplement_no_retry')
        if c['action']=='report' and c.get('outcome')=='pass':
            p=self.processor;work=p.controller.task
            if work is not None and not work.done() or p.pcm or p.speaking or any(not j.done() for j in p.jobs):
                raise ValueError('input_and_work_must_be_settled_before_report')
        result=super().command(c)
        if c['action']=='report':
            self.finished.append(c['trial']);self.failed=c['outcome']!='pass'
            if self.failed:self.processor.pipeline_failed.set()
        return result

def gate_uncued_input(processor,events):
    """Capture stays open. Only the evaluator's uncued input is withheld from VAD.

    Every frame during a cued trial, including pauses and overlap, passes through
    unchanged. Lifecycle/upstream frames always pass. Normal GUI never uses this.
    """
    from pipecat.frames.frames import InputAudioRawFrame
    from pipecat.processors.frame_processor import FrameDirection
    original=processor.process_frame;last=None
    async def process(frame,direction):
        nonlocal last
        if isinstance(frame,InputAudioRawFrame) and direction==FrameDirection.DOWNSTREAM:
            opened=events.active is not None
            if opened!=last:
                events.emit('benchmark_input_gate',state='open' if opened else 'closed',reason='cued_trial' if opened else 'awaiting_explicit_cue')
                last=opened
            if not opened:return
        await original(frame,direction)
    processor.process_frame=process
    return processor

async def run(root,run_folder,phase,grant_path):
    root=Path(root);run_folder=Path(run_folder);m=verify(root,run_folder,phase,grant_path)
    if sys.platform!='linux':raise ValueError('Pi_required')
    from me3_assistant import runner as normal
    from me3_assistant.config import load
    from benchmark_final.runner import Events,commands
    from benchmark_final.observe import attach
    class SelectedEvents(SelectionGuard,Events):pass
    events=SelectedEvents(run_folder/'F','F');watcher=None
    def attach_cued(processor,transport,events):
        attach(processor,transport,events);gate_uncued_input(processor,events)
    def task_verification():
        from benchmark_m6.storage import write_new
        result=verify_task(root,m['task_database_sha256']);path=run_folder/'task-independent-verification.json'
        if not path.exists():write_new(path,result)
        return result
    try:
        events.emit('normal_benchmark_supplement',source_revision=SOURCE,parent_run=m['parent_run'],selected_trial_ids=SELECTED,original_F00_remains_fail=True,acoustic=False)
        with bind_runtime(normal,phase,grant_path,events,attach_cued):
            watcher=asyncio.create_task(commands(events,run_folder/'F'))
            await normal.run(load(root/'config/assistant.json'),observer=EventBridge(events,task_verification))
    finally:
        if watcher:watcher.cancel();await asyncio.gather(watcher,return_exceptions=True)
        events.emit('normal_benchmark_session_finished');events.close()
        verify_task(root,m['task_database_sha256'])
        if read(root/'evidence/completion/autonomous-20261020-001/budget.json')!=m['development_usage_unchanged']:raise ValueError('development_counter_changed')

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',required=True,type=Path);p.add_argument('--scope',required=True,type=Path);p.add_argument('--grant',required=True,type=Path);a=p.parse_args()
    from task_mcp.client import ROOT
    from loguru import logger
    logger.remove();logging.disable(logging.CRITICAL)
    try:asyncio.run(run(ROOT,a.run,a.scope,a.grant));return 0
    except KeyboardInterrupt:return 130
    except Exception as exc:
        print(json.dumps({'status':'FAIL','error_type':type(exc).__name__,'payload_omitted':True}),file=sys.stderr);return 2
if __name__=='__main__':raise SystemExit(main())
