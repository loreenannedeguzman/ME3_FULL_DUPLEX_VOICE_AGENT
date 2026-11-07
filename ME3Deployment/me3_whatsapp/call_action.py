"""Planner proposal only. Voice owner exclusively controls call confirmation."""
import re
from me3_assistant.intent import ClarificationNeeded,request_binding
from .call_lifecycle import digest

class WhatsAppCall:
    budget_role='call_start'
    def __init__(self,registry,spec):
        self.registry=registry;self.spec=spec;self.server=spec['server']
        tools={'prepare_call','commit_call','read_call','end_call','abort_call','reconcile_call'}
        if any((self.server,n) not in registry.schemas for n in tools) or tools&set(registry.sessions[self.server][1]['read_tools']):raise ValueError('private_call_lifecycle_required')
    @property
    def available(self):
        manager=getattr(self.registry,'call_session',None)
        return manager is not None and manager.callbacks is not None and not manager.held
    def declaration(self):
        if not getattr(self.registry,'call_session',None) or self.registry.call_session.callbacks is None:raise ValueError('configured_normal_GUI_voice_call_owner_required')
        return dict(name=self.spec['name'],description='Propose a WhatsApp VOICE call to the already selected visibly verified contact, only when the current user asks to call. It requires a completed exact recipient/phone/call-type review and separate direct confirmation. After confirmation ME3 closes its microphone and speech services before native WhatsApp calling. The user ends the call with the GUI End call button. No video, guessed contact, background lookup, auto-dial, model confirmation, or automatic retry.',parameters=dict(type='object',properties=dict(selection_id=dict(type='string',pattern='^[0-9a-f]{32}$')),required=['selection_id'],additionalProperties=False))
    def validate_request(self,request,g):
        c=self.registry.communication_context
        if not c or c['generation']!=g or c['request']!=request:raise ValueError('current_owner_call_context_required')
    async def prepare(self,args,g,operation_id):
        self.validate_request(self.registry.communication_context['request'],g)
        c=self.registry.communication_context;choices=self.registry.contact_choices.snapshot()
        if choices.get('available') is not True:raise ClarificationNeeded('fresh_recipient_required')
        selected=choices.get('selected_verified_contact')
        if not selected:raise ClarificationNeeded('selected_recipient_required')
        if selected.get('selection_id')!=args['selection_id']:raise ClarificationNeeded('selected_recipient_mismatch')
        expected=request_binding(self.registry.communication_owner,c['goal'],g,c['request'])
        data=await self.registry.rpc(self.server,'prepare_call',g,dict(operation_id=operation_id,selection_id=args['selection_id'],host_owner=self.registry.communication_owner,host_goal=c['goal'],current_user_request=c['request']))
        i=data.get('intent',{});r=i.get('recipient',{})
        if (data.get('operation_id')!=operation_id or data.get('request_binding')!=expected or i.get('kind')!='voice'
            or r.get('selection_id')!=args['selection_id'] or data.get('expected_digest')!=digest(i)
            or not re.fullmatch('[0-9a-f]{64}',data.get('token',''))):raise ValueError('exact_call_proposal_binding_required')
        return data,data['expected_digest'],'Start a WhatsApp voice call to '+r['label']+' ('+r['phone']+'). ME3 voice will pause during the call; use End call in this GUI to end it'
    async def handoff(self,p,g):
        # Engine.confirm_action advances its own generation without replacing
        # the original request context. That request was bound at prepare;
        # this edge validates the NEW host-confirmation generation instead.
        if (not self.registry.current(g) or p.get('_confirmed_generation')!=g
            or p.get('_confirmation_source')!='direct_current_user_after_completed_prompt'
            or p.get('action')!='propose_whatsapp_call'):
            raise ValueError('current_separate_host_call_confirmation_required')
        return await self.registry.call_session.queue(p,g)
    async def execute(self,*args):raise ValueError('native_call_requires_closed_voice_handoff')
    async def verify(self,*args):return False # Ordinary voice/action code must never verify a queued handoff.

