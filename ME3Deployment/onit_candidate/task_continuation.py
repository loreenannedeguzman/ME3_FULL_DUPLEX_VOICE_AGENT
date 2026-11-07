"""Staged explicit-task lifecycle. No startup restore, background job or replay.

Normal GUI/voice constructors expose an explicit host opt-in, off by default.
A casual question never starts durable tracking. Software speech completion
is never task completion; live acceptance is separate from this staged source.
"""
import asyncio,contextvars,copy
from pathlib import Path
from onit_candidate.conversation import Conversation,candidate_session_end_kind
from task_mcp.reasoner import latest_text
from onit_candidate.task_checkpoints import TaskCheckpoints,need
from onit_candidate.task_evidence import TaskEvidence

_attempt=contextvars.ContextVar('task_checkpoint_attempt',default=None)
_session_ending=contextvars.ContextVar('task_session_ending',default=False)

class TaskContinuation:
    def __init__(self,store,context,source_revision,evidence):
        self.store,self.context,self.source_revision,self.evidence=store,context,source_revision,evidence
        self.active=None;self.armed=None;self.armed_next=False;self.selected_reference=None;self.parked_goal=None
    def retire_parked_goal_for_new_tracking(self):
        # Only explicit owner opt-in starts a NEW durable task. Ordinary
        # corrections keep the accepted conversational goal/history semantics.
        if self.parked_goal is not None and self.context.goal_id==self.parked_goal:
            self.context.parent_goal_id=self.context.goal_id;self.context.goal_id=None
            self.context.state='idle';self.context.retained.clear();self.context.clarification=None
        self.parked_goal=None
    def arm(self,request):
        need(isinstance(request,str) and request.strip() and len(request)<=4000,'fresh_explicit_task_request_required')
        need(self.active is None or self.active['state'] in {'canceled','completed'},'finish_or_cancel_existing_durable_task')
        self.retire_parked_goal_for_new_tracking()
        self.armed=request;self.selected_reference=None
    def arm_next(self):
        need(self.armed is None and not self.armed_next,'task_tracking_already_armed')
        need(self.active is None or self.active['state'] in {'canceled','completed'},'finish_or_cancel_existing_durable_task')
        self.retire_parked_goal_for_new_tracking()
        self.armed_next=True;self.selected_reference=None
    def park_after_takeover(self):
        row=self.active
        need(row is not None and row['state']=='interrupted','exact_interrupted_task_required_before_parking')
        self.parked_goal=row['goal_id'];self.active=None
        self.armed=None;self.armed_next=False;self.selected_reference=None
    def disarm_next(self):
        pending_selection=self.armed is not None and self.selected_reference is not None
        self.armed_next=False;self.armed=None
        if pending_selection:
            self.selected_reference=None;self.active=None
            self.context.parent_goal_id=self.context.goal_id;self.context.goal_id=None
            self.context.state='idle';self.context.retained.clear();self.context.clarification=None
    def started(self,goal,request,token):
        if self.armed_next:
            need(token is not None and isinstance(request,str) and request.strip() and len(request)<=4000,'fresh_direct_task_request_required')
            self.armed=request;self.armed_next=False
        if self.armed is not None:
            need(token is not None,'current_conversation_attempt_required')
            need(request==self.armed,'selected_task_request_changed')
            if self.selected_reference is None:
                self.active=dict(goal_id=goal,conversation_id=self.context.conversation_id,request=request,
                                 revision=0,state=None,artifacts=[],token=token)
            else:
                row=self.selected_reference['checkpoint']
                observed=self.evidence.reconcile(goal,row['evidence'],state=row['state'])
                self.active['artifacts']=observed['artifacts'];self.active['request']=request;self.active['token']=token
            self.armed=None
        elif not (self.active and self.active['goal_id']==goal and self.active['state'] in {'running','interrupted'}):return False
        else:self.active.update(request=request,token=token)
        need(goal==self.context.goal_id,'checkpoint_current_goal_required')
        self.mark('running',token=token,force=True);return True
    def mark(self,state,*,token=None,force=False):
        row=self.active
        if row is None or token is not None and token is not row['token']:return
        if row['state'] in {'completed','canceled'} or row['state']==state and not force:return
        ref=self.evidence.capture(row['goal_id'],state,row['artifacts'])
        saved=self.store.record(goal_id=row['goal_id'],conversation_id=row['conversation_id'],
            source_revision=self.source_revision,request=row['request'],state=state,
            expected_revision=row['revision'],evidence=[ref])
        row.update(revision=saved['revision'],state=state)
    def selected(self,goal,digest):
        ref=self.store.reference(goal)
        need(ref is not None and ref['checkpoint_sha256']==digest,'exact_current_checkpoint_required')
        row=ref['checkpoint'];need(row['source_revision']==self.source_revision,'checkpoint_source_change_requires_host_review')
        need(row['state'] in {'interrupted','failed','awaiting_owner'},'settled_interrupted_or_failed_checkpoint_required')
        observed=self.evidence.reconcile(goal,row['evidence'],state=row['state'])
        return ref,observed
    def install(self,ref,observed,request):
        row=ref['checkpoint'];self.context.parent_goal_id=self.context.goal_id
        self.context.goal_id=row['goal_id'];self.context.state='interrupted';self.context.clarification=None;self.context.retained.clear()
        self.active={k:row[k] for k in ('goal_id','conversation_id','request','revision','state')}
        self.active.update(artifacts=observed['artifacts'],token=None)
        self.selected_reference=copy.deepcopy(ref)
        self.selected_reference['artifact_reconciliation']=copy.deepcopy(observed)
        self.armed=request;self.parked_goal=None

class CheckpointConversation(Conversation):
    def __init__(self,root,*args,checkpoint_store,source_revision,checkpoint_evidence=None,**kwargs):
        need(Path(checkpoint_store.root).resolve()==Path(root).resolve(),'checkpoint_store_same_project_required')
        super().__init__(root,*args,**kwargs)
        evidence=checkpoint_evidence or TaskEvidence(root,source_revision)
        need(evidence.root.resolve()==Path(root).resolve() and evidence.source==source_revision,'checkpoint_evidence_same_source_required')
        self.task_continuation=TaskContinuation(checkpoint_store,self.context,source_revision,evidence)
        self.checkpoint_fault=False
        answer=self.engine.answer
        async def tracked_answer(request,*positional,**named):
            tracked=self.transition('task_started',self.task_continuation.started,named['goal_id'],request,_attempt.get())
            if tracked and self.task_continuation.selected_reference is not None:
                reference=self.task_continuation.store.reference(named['goal_id'])
                row=reference['checkpoint']
                reference['artifact_reconciliation']=self.task_continuation.evidence.reconcile(named['goal_id'],row['evidence'],state=row['state'])
                named['context_state']={**named['context_state'],'task_checkpoint':copy.deepcopy(reference)}
            return await answer(request,*positional,**named)
        self.engine.answer=tracked_answer
        confirm=self.engine.confirm_action
        async def tracked_confirm(*positional,**named):
            row=self.task_continuation.active;token=_attempt.get()
            if row and row['goal_id']==self.context.goal_id and row['state']=='waiting_confirmation':
                need(token is not None,'current_conversation_attempt_required')
                row['token']=token
                self.transition('confirmation_started',self.task_continuation.mark,'running',token=token)
            return await confirm(*positional,**named)
        self.engine.confirm_action=tracked_confirm
        original_rpc=self.engine.registry.rpc
        async def observe_rpc(server,tool,g,args):
            active=self.task_continuation.active;token=_attempt.get();owner=self.active_generation
            stamp=(active,token,active['goal_id'],owner,g) if (active and active['state']=='running' and token is active['token'] and active['goal_id']==self.context.goal_id and self.current(owner) and self.engine.current(g)) else None
            result=await original_rpc(server,tool,g,args)
            current=self.task_continuation.active
            spec=self.engine.registry.sessions.get(server,(None,{}))[1]
            if (stamp is not None and current is stamp[0] and current['state']=='running'
                and _attempt.get() is stamp[1] is current['token'] and current['goal_id']==stamp[2]==self.context.goal_id
                and self.active_generation==stamp[3] and self.current(stamp[3]) and g==stamp[4] and self.engine.current(g)
                and spec.get('module')=='me3_assistant.workspace_server' and spec.get('identity')=='me3'
                and spec.get('role')=='utility' and tool in {'read_workspace_file','write_workspace_file'}
                and result.get('ok') is True):
                if tool=='write_workspace_file':need(result.get('verified_by_fresh_read') is True,'task_write_readback_required')
                ref=dict(path=result.get('path'),sha256=result.get('sha256'))
                self.transition('artifact_readback',self.task_continuation.evidence.versions,[ref])
                updated={r['path']:r for r in current['artifacts']};updated[ref['path']]=ref
                need(len(updated)<=16,'bounded_task_artifacts_required');current['artifacts']=list(updated.values())
                self.transition('artifact_checkpoint',self.task_continuation.mark,'running',token=current['token'],force=True)
            return result
        self.engine.registry.rpc=observe_rpc
    def transition(self,stage,operation,*args,**kwargs):
        if self.checkpoint_fault:raise RuntimeError('checkpoint_fault_requires_host_reconciliation')
        try:return operation(*args,**kwargs)
        except Exception as error:
            self.checkpoint_fault=True;self.failed.set()
            self.events.emit('task_checkpoint_failed',stage=stage,error_type=type(error).__name__)
            raise
    def arm_task(self,request):
        if self.checkpoint_fault:raise RuntimeError('checkpoint_fault_requires_host_reconciliation')
        need(self.engine.task is None and self.pending is None,'settle_current_task_before_tracking')
        self.task_continuation.arm(request)
    def arm_next_task(self):
        if self.checkpoint_fault:raise RuntimeError('checkpoint_fault_requires_host_reconciliation')
        need(self.engine.task is None and self.pending is None,'settle_current_task_before_tracking')
        self.task_continuation.arm_next()
    async def answer(self,history):
        if self.checkpoint_fault:raise RuntimeError('checkpoint_fault_requires_host_reconciliation')
        # A new direct request may redirect a prepared continuation. Revoke
        # only its unstarted reference before Context.begin chooses a goal.
        # Neither speech input nor task history is a semantic routing rule.
        if self.task_continuation.armed is not None and latest_text(history)!=self.task_continuation.armed:
            self.task_continuation.disarm_next()
        token=object();reset=_attempt.set(token);end_reset=_session_ending.set(bool(history and candidate_session_end_kind(latest_text(history))))
        try:
            result=await super().answer(history)
            # An answer or clarification leaves the durable task unfinished.
            state='waiting_confirmation' if self.pending else 'awaiting_owner'
            self.transition('answer_settled',self.task_continuation.mark,state,token=token)
            return result
        except asyncio.CancelledError:
            if not self.checkpoint_fault:
                try:self.transition('cancellation',self.task_continuation.mark,'interrupted',token=token)
                except Exception:pass
            raise
        except BaseException:
            if not self.checkpoint_fault:
                try:self.transition('failure',self.task_continuation.mark,'failed',token=token)
                except Exception:pass
            raise
        finally:_session_ending.reset(end_reset);_attempt.reset(reset)
    def interrupt(self):
        super().interrupt()
        row=self.task_continuation.active
        if not self.checkpoint_fault and row and row['state']=='running' and row['goal_id']==self.context.goal_id and self.engine.task is not None:
            try:
                self.transition('interruption',self.task_continuation.mark,'interrupted')
                self.transition('takeover_parking',self.task_continuation.park_after_takeover)
            except Exception:pass # bookkeeping must never block cancellation
    def abandon_goal(self):
        self.task_continuation.disarm_next()
        super().abandon_goal()
        self.task_continuation.armed=None;self.task_continuation.armed_next=False;self.task_continuation.selected_reference=None
        row=self.task_continuation.active
        if row and row['goal_id']==self.context.goal_id and not self.checkpoint_fault:
            if _session_ending.get() and row['state']=='failed':return
            self.transition('owner_cancellation',self.task_continuation.mark,'interrupted' if _session_ending.get() else 'canceled')
    async def prepare_task_resume(self,goal,checkpoint_sha256,owner_request):
        need(isinstance(owner_request,str) and owner_request.strip() and len(owner_request)<=4000,'fresh_explicit_owner_resume_request_required')
        need(self.engine.task is None,'settle_current_task_before_resume')
        need(getattr(self.engine.registry,'session_grant',{}).get('workspace_access') is True,'workspace_recovery_grant_required')
        gateway=self.engine.gateway
        need(gateway is None or not gateway.blockers(),'independent_action_readback_required_before_resume')
        need(self.pending is None,'cancel_current_review_before_resume')
        ref,observed=self.task_continuation.selected(goal,checkpoint_sha256)
        self.task_continuation.install(ref,observed,owner_request)
        return dict(goal_id=goal,reference_only=True,confirmation_authority=False,
                    execution_started=False,automatic_execution=False,new_exact_review_required=True)
    def finish_task(self,goal,checkpoint_sha256,*,cancel=False):
        need(type(cancel) is bool,'explicit_task_outcome_required')
        row=self.task_continuation.active;need(row is not None and row['goal_id']==goal,'exact_active_task_required')
        need(row['state'] in ({'awaiting_owner','interrupted','failed','waiting_confirmation'} if cancel else {'awaiting_owner'}),'settled_task_outcome_required')
        need(self.engine.task is None and self.pending is None,'settle_current_task_before_task_outcome')
        need(getattr(self.engine.registry,'session_grant',{}).get('workspace_access') is True,'workspace_recovery_grant_required')
        gateway=self.engine.gateway;need(gateway is None or not gateway.blockers(),'independent_action_readback_required_before_task_outcome')
        ref=self.task_continuation.store.reference(goal)
        need(ref is not None and ref['checkpoint_sha256']==checkpoint_sha256 and ref['checkpoint']['revision']==row['revision'],'exact_current_checkpoint_required')
        self.task_continuation.evidence.reconcile(goal,ref['checkpoint']['evidence'],state=ref['checkpoint']['state'])
        self.transition('owner_declared_task_outcome',self.task_continuation.mark,'canceled' if cancel else 'completed')
        self.task_continuation.armed=None;self.task_continuation.selected_reference=None
        if self.context.goal_id==goal:
            self.context.state='canceled' if cancel else 'completed';self.context.retained.clear();self.context.clarification=None
        return dict(owner_declared=True,execution_success_verified=False,automatic_execution=False)
    def finish_saved_task(self,goal,checkpoint_sha256,*,cancel=False):
        """Owner-declared outcome only; never executes/undoes artifact steps."""
        need(type(cancel) is bool,'explicit_task_outcome_required')
        need(self.engine.task is None and self.pending is None,'settle_current_task_before_task_outcome')
        need(getattr(self.engine.registry,'session_grant',{}).get('workspace_access') is True,'workspace_recovery_grant_required')
        gateway=self.engine.gateway;need(gateway is None or not gateway.blockers(),'independent_action_readback_required_before_task_outcome')
        ref=self.task_continuation.store.reference(goal)
        need(ref is not None and ref['checkpoint_sha256']==checkpoint_sha256,'exact_current_checkpoint_required')
        row=ref['checkpoint'];need(row['source_revision']==self.task_continuation.source_revision,'checkpoint_source_change_requires_host_review')
        need(row['state'] in ({'awaiting_owner','interrupted','failed','waiting_confirmation'} if cancel else {'awaiting_owner'}),'settled_task_outcome_required')
        observed=self.task_continuation.evidence.reconcile(goal,row['evidence'],state=row['state'])
        state='canceled' if cancel else 'completed'
        evidence=self.task_continuation.evidence.capture(goal,state,observed['artifacts'])
        saved=self.task_continuation.store.record(goal_id=goal,conversation_id=row['conversation_id'],
            source_revision=row['source_revision'],request=row['request'],state=state,
            expected_revision=row['revision'],evidence=[evidence])
        active=self.task_continuation.active
        if active and active['goal_id']==goal:
            active.update(state=state,revision=saved['revision']);self.task_continuation.armed=None;self.task_continuation.selected_reference=None
        if self.context.goal_id==goal:
            self.context.state=state;self.context.retained.clear();self.context.clarification=None
        return dict(owner_declared=True,state=state,execution_success_verified=False,automatic_execution=False)
    async def close(self):
        try:
            self.task_continuation.armed_next=False;self.task_continuation.armed=None
            row=self.task_continuation.active
            if not self.checkpoint_fault and row and row['state'] not in {'failed','completed','canceled'}:
                try:self.transition('session_closed',self.task_continuation.mark,'interrupted')
                except Exception:pass # fault is latched; cleanup must still settle
        finally:await super().close()

def conversation(root,*args,task_recovery=False,source_revision=None,**kwargs):
    """Explicit host-only opt-in; the default is the exact accepted owner."""
    need(type(task_recovery) is bool,'task_recovery_boolean_required')
    if not task_recovery:return Conversation(root,*args,**kwargs)
    need(isinstance(source_revision,str) and len(source_revision)==64,'task_recovery_source_required')
    return CheckpointConversation(root,*args,checkpoint_store=TaskCheckpoints(root),source_revision=source_revision,**kwargs)
