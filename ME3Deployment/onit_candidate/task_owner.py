"""Bounded recovery commands INSIDE existing authenticated /api/command only.

No new HTTP endpoint, model tool, planner, startup restore or implicit replay.
The owner must start an ordinary authorized session. Select installs reference
metadata only; a separate normal Send executes the exact fresh owner request.
"""
import re
from onit_candidate.task_checkpoints import need

COMMANDS=frozenset({'track_task','track_next_task','cancel_task_tracking','task_list','task_reference','select_task','finish_task','finish_saved_task'})

class TextOwner:
    """Read actual text-session ownership; never pretend an active turn is idle."""
    def __init__(self,console):self.console=console;self.provider=console.router;self.shared_context=console.shared
    @property
    def task(self):return self.console.turn
    @property
    def closed(self):return self.console.stop_event.is_set() or self.console.router is not self.provider

def identifier(value,n):return type(value) is str and re.fullmatch('[0-9a-f]{'+str(n)+'}',value) is not None

async def command(console,data,root):
    need(console.task_recovery is True,'task_recovery_not_enabled')
    need(type(data) is dict and data.get('action') in COMMANDS,'exact_task_owner_command_required')
    need(not console.calls.held and console.task is not None and not console.task.done() and console.stop_event is not None and not console.stop_event.is_set(),'active_authorized_task_session_required')
    need(console.turn is None or console.turn.done(),'settle_current_task_before_selection')
    need(identifier(data.get('expected_conversation_id'),32) and data['expected_conversation_id']==console.shared.conversation_id,'owner_conversation_changed_no_resume')
    if console.mode=='voice':
        p=console.processor;need(console.voice_armed and p is not None and p.candidate_listening,'wait_for_listening')
        control=p.controller;provider=control.provider
        need(console.controller is control and control.shared_context is console.shared,'current_shared_task_owner_required')
    else:
        need(console.mode=='text' and console.state=='ready' and console.pending is None and console.router is not None,'text_session_not_ready')
        control=TextOwner(console);provider=control.provider
    from onit_candidate.task_continuation import CheckpointConversation
    need(isinstance(provider,CheckpointConversation) and provider.context is console.shared and provider.controller is (console.router.controller if console.mode=='text' else control),'checkpoint_current_constructor_required')
    need(not control.closed and (control.task is None or control.task.done()) and provider.engine.task is None,'settle_current_task_before_selection')
    from me3_assistant.guard import verify_sources,authorize
    from onit_candidate.identity import verify_candidate
    source=verify_sources(root)
    need(source['revision']==console.source['revision']==provider.task_continuation.source_revision and verify_candidate(root)==console._candidate_hashes,'task_recovery_source_changed')
    grant,_=authorize(console.config,source,mode='normal' if console.mode=='voice' else 'text')
    need(grant.get('workspace_access') is True and provider.engine.registry.session_grant.get('workspace_access') is True,'workspace_recovery_grant_required')
    action=data['action']
    if action in {'track_next_task','cancel_task_tracking'}:
        need(set(data)=={'action','expected_conversation_id'},'exact_owner_task_command_required')
        if action=='track_next_task':provider.arm_next_task()
        else:provider.task_continuation.disarm_next()
        return dict(armed_next=provider.task_continuation.armed_next,execution_started=False,automatic_execution=False)
    if action=='task_list':
        need(set(data)=={'action','expected_conversation_id','offset'},'exact_owner_task_command_required')
        return provider.task_continuation.store.listing(data['offset'])
    if action=='track_task':
        need(set(data)=={'action','expected_conversation_id','owner_request'},'exact_owner_task_command_required')
        provider.arm_task(data['owner_request'])
        return dict(selected=True,execution_started=False,automatic_execution=False)
    fields={'action','expected_conversation_id','goal_id'}
    need(identifier(data.get('goal_id'),32),'exact_owner_task_selection_required')
    if action=='task_reference':
        need(set(data)==fields,'exact_owner_task_command_required')
        # Explicit exact goal only; no implicit directory enumeration.
        ref=provider.task_continuation.store.reference(data['goal_id']);observed=None
        if ref is not None:
            row=ref['checkpoint'];need(row['source_revision']==provider.task_continuation.source_revision,'checkpoint_source_change_requires_host_review')
            observed=provider.task_continuation.evidence.reconcile(data['goal_id'],row['evidence'],state=row['state'])
        return dict(reference=ref,artifact_reconciliation=observed,execution_started=False,automatic_execution=False)
    need(identifier(data.get('checkpoint_sha256'),64),'exact_owner_task_selection_required')
    if action=='select_task':
        need(set(data)==fields|{'checkpoint_sha256','owner_request'},'exact_owner_task_command_required')
        return await provider.prepare_task_resume(data['goal_id'],data['checkpoint_sha256'],data['owner_request'])
    need(set(data)==fields|{'checkpoint_sha256','cancel'},'exact_owner_task_command_required')
    method=provider.finish_saved_task if action=='finish_saved_task' else provider.finish_task
    return method(data['goal_id'],data['checkpoint_sha256'],cancel=data['cancel'])
