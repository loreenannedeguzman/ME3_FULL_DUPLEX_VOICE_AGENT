"""Host-only admission around a non-executing sole-Onit review decision."""
import copy,hashlib,json,math
from task_mcp.reasoner import latest_text,prompt_delivered
from onit_candidate.protocol import CandidateError

# Deliberate draft blocker until the actual Onit-derived correction is reviewed.
CORE_APPROVED_SHA256='cbce306be2c24999f0c9e1b17af2efb93e9b5f794d0f11adf50122f8460b4d67'

class ReviewExpired(Exception):
    """Only an observed exact still-uncommitted host review may raise this."""
    def __init__(self,turn):self.turn=turn;super().__init__('confirmation_review_expired_before_dispatch')
def need(value,code):
    if not value:raise CandidateError(code)
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def text_digest(value):return hashlib.sha256(value.encode()).hexdigest()
def registry_grant(registry,root,mode):
    from me3_assistant.guard import verify_sources,authorize
    source=verify_sources(root);grant,_=authorize(registry.config,source,mode=mode)
    need(digest(grant)==digest(registry.session_grant),'confirmation_current_grant_changed')
    return source['revision'],digest(grant)

def source_grant(provider):
    return registry_grant(provider.engine.registry,provider.engine.root,'normal' if provider.voice else 'text')

class ConfirmationExpiryFence:
    """Explanatory retirement only; never grants a confirmation or an effect."""
    def __init__(self,gateway,history,g):
        self.gateway=gateway;self.history=history;self.g=g;self.armed=False
        p=gateway.actions.pending
        if not p or not gateway.pending_key or not prompt_delivered(history,p['prompt']) or history[-2].get('partial_output'):return
        self.pending=p;self.original=copy.deepcopy(p);self.raw=latest_text(history);self.owner=getattr(gateway.registry,'communication_owner',None)
        self.context=copy.deepcopy(getattr(gateway.registry,'communication_context',None));self.key=gateway.pending_key
        row=gateway.ledger.load().get(self.key,{})
        from me3_assistant.actions import Journal
        record=Journal(gateway.journal.path).records.get(p['operation_id'],{})
        need(p.get('phase')=='confirm' and type(p.get('expires')) in (int,float) and math.isfinite(p['expires']) and self.key[0]==gateway.goal and row.get('state')=='WAITING_CONFIRMATION' and row.get('operation_id')==p['operation_id'] and row.get('action')==p['action'],'confirmation_expiry_review_not_bound')
        need(record.get('state')=='PROPOSED' and record.get('action')==p['action'] and record.get('expected_digest')==p['expected_digest'],'confirmation_expiry_journal_not_proposed')
        need(type(self.owner) is str and len(self.owner)==32 and isinstance(self.context,dict) and self.context.get('goal')==gateway.goal and type(self.context.get('generation')) is int and self.context['generation']<=g and type(self.context.get('request')) is str,'confirmation_expiry_context_required')
        self.source=self.observe_source();self.prefix=gateway.journal.path.read_bytes();self.armed=True
    def observe_source(self):
        registry=self.gateway.registry;mode=registry.session_grant.get('mode')
        need(mode in {'normal','text'},'confirmation_expiry_grant_mode_required')
        return registry_grant(registry,self.gateway.ledger.root,mode)
    def current(self):
        gate=self.gateway;gate.check(self.g)
        need(gate.registry.communication_owner==self.owner and gate.registry.communication_context==self.context and latest_text(self.history)==self.raw and prompt_delivered(self.history,self.original['prompt']) and not self.history[-2].get('partial_output'),'confirmation_expiry_owner_changed')
        need(self.observe_source()==self.source,'confirmation_expiry_source_or_grant_changed')
    def expired_before_actions(self):
        if not self.armed or self.gateway.actions.clock()<self.original['expires']:return False
        self.current()
        need(self.gateway.actions.pending is self.pending and digest(self.pending)==digest(self.original) and self.gateway.pending_key==self.key and self.gateway.journal.path.read_bytes()==self.prefix,'confirmation_expiry_live_review_changed')
        return True
    def retired_without_dispatch(self,p,row):
        if not self.armed or p is not self.pending or self.gateway.actions.pending is not None or row.get('operation_id')!=self.original['operation_id'] or self.gateway.actions.clock()<self.original['expires']:return False
        self.current();raw=self.gateway.journal.path.read_bytes()
        need(raw.startswith(self.prefix),'confirmation_expiry_journal_prefix_changed')
        suffix=[json.loads(line) for line in raw[len(self.prefix):].splitlines() if line]
        own=[x for x in suffix if x.get('operation_id')==self.original['operation_id']]
        return bool(own) and all(x.get('state')=='CANCELED' and x.get('action')==self.original['action'] and x.get('expected_digest')==self.original['expected_digest'] for x in own)
class Core:
    def __init__(self,fields,raw,issuer,evidence):
        need(CORE_APPROVED_SHA256 is not None,'confirmation_Onit_core_not_approved')
        from pathlib import Path
        from onit_candidate import pending_action_turn as module
        need(CORE_APPROVED_SHA256 is not None and hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()==CORE_APPROVED_SHA256,'confirmation_Onit_core_not_approved')
        self.module=module;self.turn=module.PendingReviewTurn(module.ReviewBinding(**fields),raw,issuer,evidence)
    def bind(self,revision,g):return self.turn.bind(revision,g)
    def declaration(self):return dict(name='decide_pending_action',description='Return one informational decision about the exact current host review. This control never executes or grants an action.',parameters=self.turn.declaration())
    def validate(self,args,fields,now,issuer):return self.turn.validate_decision(args,self.module.ReviewBinding(**fields),now,issuer)
    def close(self):return self.turn.close()
class PendingActionTurn:
    """Never created from native arguments; never owns action execution/grants."""
    def __init__(self,provider,raw,history,g,captured):
        self.provider=provider;self.engine=provider.engine;self.gateway=self.engine.gateway
        self.history=history;self.raw=raw;self.g=g;self.issuer=object();self.phase='prepared';self.decision=None;self.confirmation_used=False
        self.pending=self.gateway.actions.pending;need(self.pending is not None,'confirmation_pending_required');self.original=copy.deepcopy(self.pending)
        self.communication_context=copy.deepcopy(self.engine.registry.communication_context)
        self.reference_request=copy.deepcopy(getattr(self.engine.registry,'reference_request',None))
        self.source,self.grant=source_grant(provider)
        need(captured is False,'confirmation_capture_during_review')
        fields=self.binding(allow_expired=True);self.initial=fields
        if self.gateway.actions.clock()>=self.original['expires']:raise ReviewExpired(self)
        evidence=dict(direct_separate_user_input=True,delivered_complete=True,nonpartial=True,capture_finished_after_review=True,current_owner=True,current_source=True,current_grant=True,phase='confirm',ledger='WAITING_CONFIRMATION',journal='PROPOSED',operation_id=fields['operation_id'],action=fields['action'],expected_digest=fields['expected_digest'])
        self.journal_before=self.gateway.journal.path.read_bytes()
        self.core=Core(fields,raw,self.issuer,evidence);self.bound=None;self.revision_used=False
    def binding(self,*,allow_expired=False):
        p=self.provider;e=self.engine;g=self.gateway;pending=g.actions.pending
        need(getattr(p,'_pending_action_turn',None) in (None,self) and p.current(self.g) and p.active_generation==self.g,'confirmation_current_owner_required')
        need(pending is self.pending and pending is not None and digest(pending)==digest(self.original),'confirmation_exact_pending_changed')
        need(self.history and self.history[-1].get('type')=='user_input' and latest_text(self.history)==self.raw and prompt_delivered(self.history,pending['prompt']) and not self.history[-2].get('partial_output'),'confirmation_separate_delivered_turn_required')
        need(pending.get('phase')=='confirm' and type(pending.get('expires')) in (int,float) and math.isfinite(pending['expires']),'confirmation_pending_wrong_phase')
        key=g.pending_key;rows=g.ledger.load();row=rows.get(key,{})
        from me3_assistant.actions import Journal
        record=Journal(g.journal.path).records.get(pending['operation_id'],{})
        need(key is not None and key[0]==g.goal==p.context.goal_id and row.get('state')=='WAITING_CONFIRMATION' and row.get('operation_id')==pending['operation_id'] and row.get('action')==pending['action'],'confirmation_ledger_not_current_review')
        need(record.get('operation_id')==pending['operation_id'] and record.get('state')=='PROPOSED' and record.get('action')==pending['action'] and record.get('expected_digest')==pending['expected_digest'],'confirmation_journal_not_proposed')
        source,grant=source_grant(p);need(source==self.source and grant==self.grant,'confirmation_source_or_grant_changed')
        context=e.registry.communication_context;owner=getattr(e.registry,'communication_owner',None)
        need(isinstance(owner,str) and len(owner)==32 and all(c in '0123456789abcdef' for c in owner),'confirmation_communication_owner_required')
        need(context==self.communication_context if self.phase!='confirming' else context==dict(self.communication_context,generation=e.generation,request=self.raw),'confirmation_original_context_changed')
        need(getattr(e.registry,'reference_request',None)==self.reference_request,'confirmation_original_reference_changed')
        # Check time after source/grant/journal I/O, not before it.
        need(allow_expired or g.actions.clock()<pending['expires'],'confirmation_pending_expired')
        return dict(operation_id=pending['operation_id'],action=pending['action'],expected_digest=pending['expected_digest'],prompt_sha256=text_digest(pending['prompt']),goal_id=g.goal,conversation_id=p.context.conversation_id,source_revision=source,context_revision=p.context.revision,controller_generation=self.g,engine_generation=e.generation,communication_owner=owner,raw_request_sha256=text_digest(self.raw),expires=pending['expires'])
    def prepare_engine(self,engine,request,goal):
        need(engine is self.engine and request==self.raw and goal==self.initial['goal_id'] and self.phase=='prepared','confirmation_host_turn_required')
        fields=self.binding(allow_expired=True);need(fields==dict(self.initial,context_revision=self.initial['context_revision']+1),'confirmation_current_context_revision_required')
        if self.gateway.actions.clock()>=self.original['expires']:raise ReviewExpired(self)
        self.bound=(fields['context_revision'],engine.generation+1);self.core.bind(*self.bound);self.phase='planning'
    def enter_engine(self,g):
        need(self.phase=='planning' and self.bound==(self.provider.context.revision,g) and self.engine.generation==g,'confirmation_engine_generation_changed')
    def dispatch_ready(self,g):
        self.enter_engine(g);fields=self.binding(allow_expired=True)
        need(fields==dict(self.initial,context_revision=self.bound[0],engine_generation=g),'confirmation_dispatch_binding_changed')
        if self.gateway.actions.clock()>=self.original['expires']:
            self.decision='expired';self.phase='decided';return False
        return True
    def declaration(self):return self.core.declaration()
    def validate_batch(self,calls,schemas):
        declaration=self.declaration();name=declaration['name']
        need(len(calls)==1 and calls[0].get('name')==name and set(schemas)=={name},'confirmation_decision_must_be_alone')
        from jsonschema import Draft202012Validator
        Draft202012Validator(declaration['parameters']).validate(calls[0]['args'])
    def decide(self,args,g):
        self.enter_engine(g);fields=self.binding(allow_expired=True)
        need(fields==dict(self.initial,context_revision=self.bound[0],engine_generation=self.bound[1]),'confirmation_binding_changed')
        now=self.gateway.actions.clock()
        if now>=self.pending['expires']:
            self.decision='expired';self.phase='decided';return
        info=self.core.validate(args,fields,now,self.issuer)
        # The checked informational decision is never an action permit.
        decision=getattr(info,'decision',None)
        need(decision in {'confirm_unchanged','ask_or_explain','cancel','revise'},'confirmation_informational_decision_required')
        self.decision=decision;self.phase='decided'
    async def finish(self,result):
        if self.phase=='retired':
            need(result.get('_pending_action_turn') is self and result.get('status')=='no_pending_review' and self.engine.task is None,'confirmation_expired_host_result_required')
            return {key:value for key,value in result.items() if key!='_pending_action_turn'}
        if result.get('status')!='pending_review_decision':
            need(self.phase=='planning' and self.engine.task is None,'confirmation_unselected_turn_not_closed')
            fields=self.binding(allow_expired=True)
            need(fields==dict(self.initial,context_revision=self.bound[0],engine_generation=self.bound[1]),'confirmation_unselected_binding_changed')
            if self.gateway.actions.clock()>=self.pending['expires']:
                return dict(self.expire(),model_calls=result['model_calls'])
            return dict(answer=self.pending['prompt'],status='waiting_confirmation',model_calls=result['model_calls'])
        need(result.get('status')=='pending_review_decision' and result.get('_pending_action_turn') is self and self.phase=='decided' and self.engine.task is None,'confirmation_closed_native_decision_required')
        fields=self.binding(allow_expired=True);need(fields==dict(self.initial,context_revision=self.bound[0],engine_generation=self.bound[1]),'confirmation_binding_changed_after_native')
        if self.decision=='expired' or self.gateway.actions.clock()>=self.pending['expires']:
            return dict(self.expire(),model_calls=result['model_calls'])
        if self.decision=='confirm_unchanged':
            data=await self.engine.confirm_action(self.history,_host_acceptance=self)
            return dict(data,answer=data['reply'],status='waiting_confirmation' if self.provider.pending else data['status'],model_calls=result['model_calls'])
        if self.decision=='revise':
            return dict(status='pending_review_revision',answer='',_pending_action_turn=self,model_calls=result['model_calls'])
        if self.decision=='cancel':
            self.gateway.cancel_pending()
            return dict(answer='The unconfirmed proposal is canceled.',status='proposal_canceled',model_calls=result['model_calls'])
        return dict(answer=self.pending['prompt'],status='waiting_confirmation',model_calls=result['model_calls'])
    def expire(self):
        # Only the identical still-PROPOSED live review can be retired here.
        self.binding(allow_expired=True)
        need(self.gateway.actions.clock()>=self.original['expires'],'confirmation_expiry_not_observed')
        self.gateway.cancel_pending();self.phase='retired'
        reply='That proposal expired. Please make a fresh request for a new review.'
        return dict(answer=reply,reply=reply,status='no_pending_review',execution_started=False)
    def before_confirmation(self,engine,history):
        need(engine is self.engine and history is self.history and self.phase=='decided' and self.decision=='confirm_unchanged' and engine.task is None and getattr(self.provider,'_pending_action_turn',None) is self,'confirmation_private_host_acceptance_required')
        fields=self.binding(allow_expired=True);need(fields==dict(self.initial,context_revision=self.bound[0],engine_generation=self.bound[1]),'confirmation_acceptance_stale')
        if self.gateway.actions.clock()>=self.original['expires']:return False
        self.phase='confirming'
        return True
    def gateway_confirmation(self,gateway,history,g):
        need(self.phase=='confirming' and not self.confirmation_used and gateway is self.gateway and history is self.history and self.engine.generation==g==self.bound[1]+1 and self.engine.current(g) and getattr(self.provider,'_pending_action_turn',None) is self,'confirmation_private_admission_not_current')
        fields=self.binding(allow_expired=True);need(fields==dict(self.initial,context_revision=self.bound[0],engine_generation=g),'confirmation_acceptance_changed')
        if self.gateway.actions.clock()>=self.original['expires']:return False
        self.confirmation_used=True
        return True
    def begin_revision(self):
        need(self.phase=='decided' and self.decision=='revise' and not self.revision_used and self.engine.task is None,'confirmation_revision_once_required')
        fields=self.binding(allow_expired=True)
        need(fields==dict(self.initial,context_revision=self.bound[0],engine_generation=self.bound[1]),'confirmation_revision_binding_changed')
        if self.gateway.actions.clock()>=self.original['expires']:return self.expire()
        role=self.gateway.actions.adapters[self.original['action']].budget_role
        # These roles may execute inside Actions.propose and cannot be used as
        # a new-review-only continuation. No authority is altered to admit them.
        if role in {'music_control','window_control'}:
            self.gateway.cancel_pending();self.phase='retired'
            return dict(answer='The unconfirmed proposal is canceled. Please make a fresh request for the change.',status='proposal_canceled',execution_started=False)
        self.gateway.cancel_pending();self.core.close();self.revision_used=True;self.phase='revision_prepared'
        return None
    def prepare_revision_engine(self,engine,request,goal,history):
        need(engine is self.engine and request==self.raw and goal==self.initial['goal_id'] and self.phase=='revision_prepared' and self.revision_used,'confirmation_revision_host_turn_required')
        need(self.provider._pending_action_turn is self and self.provider.current(self.g) and self.provider.active_generation==self.g and self.provider.context.revision==self.bound[0] and self.provider.context.goal_id==goal,'confirmation_revision_owner_changed')
        from onit_candidate.conversation import onit_history
        need(history==onit_history(self.history[:-1]) and latest_text(self.history)==self.raw and prompt_delivered(self.history,self.original['prompt']) and not self.history[-2].get('partial_output'),'confirmation_revision_original_history_required')
        source,grant=source_grant(self.provider);need((source,grant)==(self.source,self.grant),'confirmation_source_or_grant_changed')
        need(self.gateway.actions.pending is None and self.gateway.pending_key is None and not self.gateway.blockers(),'confirmation_revision_requires_retired_review')
        from me3_assistant.actions import Journal
        record=Journal(self.gateway.journal.path).records.get(self.original['operation_id'],{})
        need(record.get('state')=='CANCELED' and record.get('action')==self.original['action'] and record.get('expected_digest')==self.original['expected_digest'],'confirmation_revision_old_review_not_canceled')
        need(self.engine.registry.communication_context==self.communication_context and getattr(self.engine.registry,'reference_request',None)==self.reference_request,'confirmation_revision_original_context_changed')
        self.phase='revising'
        return self.original['action']
    def finish_revision(self,result):
        need(self.phase=='revising' and self.revision_used and self.engine.task is None and self.provider.current(self.g),'confirmation_revision_native_cleanup_required')
        source,grant=source_grant(self.provider);need((source,grant)==(self.source,self.grant),'confirmation_source_or_grant_changed')
        pending=self.gateway.actions.pending
        from me3_assistant.actions import Journal
        records=Journal(self.gateway.journal.path).records
        old=records.get(self.original['operation_id'],{})
        need(old.get('state')=='CANCELED','confirmation_revision_cannot_execute_old_review')
        # A revision can propose a new review, never carry confirmation across.
        raw=self.gateway.journal.path.read_bytes();need(raw.startswith(self.journal_before),'confirmation_journal_prefix_changed')
        for row in (json.loads(line) for line in raw[len(self.journal_before):].splitlines() if line):
            need(row.get('action')==self.original['action'] and row.get('state') in {'CANCELED','NOT_DISPATCHED','PROPOSED'},'confirmation_revision_effect_not_permitted')
        if pending is not None:
            record=records.get(pending['operation_id'],{})
            need(pending['operation_id']!=self.original['operation_id'] and pending['action']==self.original['action'] and pending['phase']=='confirm' and record.get('state')=='PROPOSED' and record.get('expected_digest')==pending['expected_digest'],'confirmation_revision_new_review_required')
            result=dict(result,answer=pending['prompt'],status='waiting_confirmation',execution_started=False)
        self.phase='revision_done';return result
    def rejected_revision_without_effect(self):
        need(self.phase=='revising' and self.revision_used and self.provider.current(self.g) and self.gateway.actions.pending is None and not self.gateway.blockers(),'confirmation_revision_failure_not_known_safe')
        source,grant=source_grant(self.provider);need((source,grant)==(self.source,self.grant),'confirmation_source_or_grant_changed')
        raw=self.gateway.journal.path.read_bytes();need(raw.startswith(self.journal_before),'confirmation_journal_prefix_changed')
        own=[json.loads(line) for line in raw[len(self.journal_before):].splitlines() if line]
        need(all(row.get('action')==self.original['action'] and row.get('state') in {'CANCELED','NOT_DISPATCHED','PROPOSED'} for row in own),'confirmation_revision_failure_may_have_effect')
        rows=self.gateway.ledger.load()
        need(any(key[0]==self.initial['goal_id'] and row.get('action')==self.original['action'] and row.get('state')=='NOT_DISPATCHED' for key,row in rows.items()),'confirmation_revision_not_dispatched_proof_required')
        return True
    def close(self):
        self.phase='closed'
        if hasattr(self,'core'):self.core.close()
def admit_engine(turn,engine,request,goal):
    need(type(turn) is PendingActionTurn,'confirmation_private_turn_required');turn.prepare_engine(engine,request,goal)
def admit_confirmation(turn,engine,history):
    need(type(turn) is PendingActionTurn,'confirmation_private_acceptance_required');return turn.before_confirmation(engine,history)
def admit_gateway(turn,gateway,history,g):
    need(type(turn) is PendingActionTurn,'confirmation_private_acceptance_required');return turn.gateway_confirmation(gateway,history,g)

def admit_revision(turn,engine,request,goal,history):
    need(type(turn) is PendingActionTurn,'confirmation_private_turn_required');return turn.prepare_revision_engine(engine,request,goal,history)
