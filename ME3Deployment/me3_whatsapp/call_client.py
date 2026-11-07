"""GUI lifetime private MCP client; never uses a canceled speech Registry."""
import copy,time
from pathlib import Path
from .call_service import CallService,CallAuthority,CONFIRMATION
from .call_events import CallEvents

def record_call_result(root,result,proof):
    """Trusted lifecycle callback with fixed canonical paths, never model args."""
    from me3_assistant.actions import Journal
    from onit_candidate.gateway import Ledger
    root=Path(root).resolve();op=proof['operation_id'];binding=proof.get('handoff',{})
    if result.get('operation_id')!=op:raise ValueError('exact_call_result_required')
    journal=Journal(root/'data/assistant-actions.jsonl');ledger=Ledger(root/'data/onit-action-intents.jsonl',root)
    row=journal.records.get(op);key=(binding.get('goal_id'),binding.get('intent_key'));intent=ledger.rows.get(key)
    if not row or row['action']!='propose_whatsapp_call' or row['expected_digest']!=proof['intent_hash'] or not intent or intent.get('operation_id')!=op or intent['action']!='propose_whatsapp_call':raise ValueError('canonical_call_operation_binding_required')
    state=result.get('state')
    if state=='NO_DIAL_ABORTED':target='NOT_DISPATCHED'
    elif state=='LOCAL_ENDED' and result.get('owned_browser_closed') is True and result.get('owned_streams_closed') is True and result.get('independent') is True:target='LOCAL_ENDED'
    elif state=='ENDED' and result.get('remote_end_confirmed') is True:target='VERIFIED'
    elif state in {'DIALING','RINGING','CONNECTED','HELD_UNCERTAIN'}:target='UNCERTAIN'
    else:raise ValueError('qualified_call_lifecycle_result_required')
    # A late commit/read response must never reopen a locally closed or
    # independently terminal call after the authenticated End call callback.
    if row['state'] in {'LOCAL_ENDED','NOT_DISPATCHED','VERIFIED'} and intent['state']==row['state']:
        if target=='UNCERTAIN' or target==row['state']:return
        raise ValueError('terminal_call_result_cannot_regress')
    if row['state']==target and intent['state']==target:return
    if result.get('execution_started') is True and row['state']=='HANDOFF_AUTHORIZED':journal.record(op,'propose_whatsapp_call','DISPATCHED',proof['intent_hash'])
    journal.record(op,'propose_whatsapp_call',target,proof['intent_hash'])
    ledger.record(key[0],key[1],'propose_whatsapp_call',target,op)

async def configure_call_session(registry,manager,root,*,on_result=None,verify_recovery=None):
    """Bind once to one normal GUI owner. Parent supplies source-bound config."""
    root=Path(root).resolve();registry.call_session=manager;authority=CallAuthority(root)
    # Voice owns registry.events and closes its descriptor before this native
    # handoff. The new MCP owner must use its own GUI-lifetime metadata sink.
    call_events=CallEvents(root,manager.console.shared.conversation_id,observer=manager.console.observe)
    # Host authority operations are private local durable writes only. No
    # browser/account/provider operations occur until the commit callback.
    class NoBrowser:
        pass
    local=CallService(root,NoBrowser(),lambda *a:None)
    config=copy.deepcopy(registry.config)
    config['servers']=[s for s in config['servers'] if s['id']=='calling' and s['module']=='me3_whatsapp.call_server']
    if len(config['servers'])!=1 or config['servers'][0]['read_tools']!=[]:raise ValueError('dedicated_private_call_MCP_required')
    async def rpc(tool,args,g):
        from me3_assistant.registry import Registry
        async with Registry(call_events,config) as client:
            client.current=lambda generation:generation==g
            return await client.rpc('calling',tool,g,args)
    def authorize(p,g):
        if not registry.current(g):raise ValueError('current_Engine_generation_required')
        return local.authorize(p,g,manager.console.shared.conversation_id)
    async def update(result,proof):
        callback=on_result if on_result is not None else lambda r,p:record_call_result(root,r,p)
        if callback is not None:
            import inspect
            value=callback(result,proof)
            if inspect.isawaitable(value):await value
        return result
    async def commit(proof):
        manager.assert_voice_closed()
        v=dict(proof,voice_closed=True)
        signed=authority.sign(v)
        result=await rpc('commit_call',dict(proof=signed),v['generation'])
        return await update(result,v)
    async def hangup(proof,request):
        manager.assert_voice_closed();sealed=manager.authenticate_gui(request)
        now=time.time();v=dict(proof,action='end',voice_closed=True,issued_at=now,expires=now+30)
        signed=authority.sign(v)
        local.mark_end_requested(signed,sealed)
        result=await rpc('end_call',dict(proof=signed,gui_request=sealed),v['generation'])
        return await update(result,v)
    async def reconcile(proof,request):
        manager.assert_voice_closed();sealed=manager.authenticate_gui(request)
        if sealed.get('action')!='check_call_status':raise ValueError('authenticated_GUI_status_check_required')
        now=time.time();v=dict(proof,action='reconcile',voice_closed=True,issued_at=now,expires=now+30)
        result=await rpc('reconcile_call',dict(proof=authority.sign(v),gui_request=sealed),v['generation'])
        return await update(result,v)
    async def verify_terminal(result,proof):
        if result.get('operation_id')!=proof.get('operation_id'):return False
        # Verify from a NEW private service read, never trust the returned
        # execution dictionary alone or a model's completion text.
        current=await rpc('read_call',dict(operation_id=proof['operation_id']),proof['generation'])
        state=current.get('state')
        clean={k:v for k,v in current.items() if k not in {'ok','generation','request_id','server_role'}}
        expected={k:v for k,v in result.items() if k not in {'ok','generation','request_id','server_role'}}
        return clean==expected and (state=='NO_DIAL_ABORTED' or state=='ENDED' and current.get('remote_end_confirmed') is True or state=='LOCAL_ENDED' and current.get('owned_browser_closed') is True and current.get('owned_streams_closed') is True and current.get('independent') is True)
    async def abort_before_dispatch(proof):
        manager.assert_voice_closed();now=time.time();v=dict(proof,action='abort',voice_closed=True,issued_at=now,expires=now+30)
        return await update(await rpc('abort_call',dict(proof=authority.sign(v)),v['generation']),v)
    manager.configure(authorize=authorize,commit=commit,hangup=hangup,verify_terminal=verify_terminal,abort_before_dispatch=abort_before_dispatch,reconcile=reconcile)
    recovery=local.recovery()
    if len(recovery)>1:raise ValueError('multiple_call_holds_require_private_reconciliation')
    if recovery:
        if verify_recovery is None:raise ValueError('prior_call_hold_requires_independent_host_recovery_audit')
        async def recover(record,new_gui_session):return await local.adopt_recovery(record,new_gui_session,verify_recovery)
        await manager.recover_hold(recovery[0],verify_recovery=recover)
    return manager

