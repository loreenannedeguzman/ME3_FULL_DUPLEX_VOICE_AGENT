"""Onit proposes; the host reviews, confirms, dispatches and verifies."""
import hashlib,re
from .action import fingerprint
from me3_assistant.intent import request_binding,message_text,ClarificationNeeded
class ContactMessage:
 budget_role='message_send'
 def __init__(self,registry,spec):
  self.registry=registry;self.spec=spec;self.server=spec['server']
  names={'prepare_contact_message','commit_contact_message','read_contact_message'}
  if any((self.server,n) not in registry.schemas for n in names) or names&set(registry.sessions[self.server][1]['read_tools']):raise ValueError('private_contact_message_lifecycle_required')
 def declaration(self):
  return dict(name=self.spec['name'],description='Prepare an exact SEND review for an already selected, visibly verified WhatsApp contact. Interpret the owner request naturally; text may be dictated across sentences or drafted by you from their intent. Use this only when sending/review-for-sending is requested; answer draft-only requests with draft text instead. Never sends until completed exact recipient/phone/text review and a separate direct confirmation. Use a fresh commanded lookup/selection if missing or expired. The host enforces the temporary qualification allowlist; it is never evidence of a selected or verified contact. No calls, inbox, guessed contacts or automatic retry.',parameters=dict(type='object',properties=dict(selection_id=dict(type='string',pattern='^[0-9a-f]{32}$'),text=dict(type='string',minLength=1,maxLength=2000)),required=['selection_id','text'],additionalProperties=False))
 def validate_request(self,request,g):
  context=self.registry.communication_context
  if not context or context['generation']!=g or context['request']!=request:raise ValueError('current_contact_send_context_required')
 async def prepare(self,args,g,operation_id):
  c=self.registry.communication_context;message_text(args['text']);expected=request_binding(self.registry.communication_owner,c['goal'],g,c['request'])
  choices=getattr(self.registry,'contact_choices',None);snapshot=choices.snapshot() if choices is not None else {}
  if snapshot.get('available') is not True:raise ClarificationNeeded('fresh_recipient_required')
  selected=snapshot.get('selected_verified_contact')
  if not selected:raise ClarificationNeeded('selected_recipient_required')
  if selected['selection_id']!=args['selection_id']:raise ClarificationNeeded('selected_recipient_mismatch')
  data=await self.registry.rpc(self.server,'prepare_contact_message',g,dict(**args,operation_id=operation_id,host_owner=self.registry.communication_owner,host_goal=c['goal'],current_user_request=c['request']))
  if data.get('request_binding')!=expected:raise ValueError('host_draft_request_binding_required')
  i=data.get('intent',{});r=i.get('recipient',{})
  if data.get('operation_id')!=operation_id or i.get('text')!=args['text'] or r.get('selection_id')!=args['selection_id'] or data.get('expected_digest')!=fingerprint(i) or not re.fullmatch('[0-9a-f]{32}',data.get('token','')):raise ValueError('exact_contact_proposal_binding_required')
  return data,data['expected_digest'],'Send this exact WhatsApp message to '+r['label']+' ('+r['phone']+'): '+i['text']
 async def execute(self,p,g):return await self.registry.rpc(self.server,'commit_contact_message',g,dict(operation_id=p['operation_id'],token=p['proposal']['token'],confirmation='confirm'))
 async def inspect_outcome(self,p,g):
  data=await self.registry.rpc(self.server,'read_contact_message',g,dict(operation_id=p['operation_id']));row=data.get('row') or {};o=data.get('observation') or {};i=row.get('intent') or {}
  if (data.get('verification_source')=='durable_owned_contact_pre_send_rejection' and p.get('state')=='UNCERTAIN'
      and row.get('state')=='UNCERTAIN' and row.get('operation_id')==p['operation_id']
      and row.get('expected_digest')==p['expected_digest'] and fingerprint(i)==p['expected_digest']
      and data.get('observation')=={}):
   proof=data.get('pre_send_rejection')
   if type(proof) is dict and proof.get('operation_id')==p['operation_id'] and proof.get('expected_digest')==p['expected_digest']:
    return dict(verified=False,pre_send_rejection=proof)
  if row.get('operation_id')!=p['operation_id'] or fingerprint(i)!=p['expected_digest'] or row.get('expected_digest')!=p['expected_digest'] or row.get('state') not in {'SUBMITTED','VERIFIED'}:return dict(verified=False)
  if data.get('verification_source')!='fresh_scoped_whatsapp_ui' or o.get('exact_outgoing_message') is not True or o.get('recipient')!=i.get('recipient') or o.get('text_sha256')!=hashlib.sha256(i['text'].encode()).hexdigest() or o.get('status') not in {'submitted','sent','delivered','read'}:return dict(verified=False)
  p['verified_message_status']=o['status'];p['verified_recipient']=i['recipient'];return dict(verified=True)
 async def verify(self,p,g):return (await self.inspect_outcome(p,g))['verified'] is True
 def success_message(self,p):
  status=p['verified_message_status'];name=p['verified_recipient']['label']
  return 'Your message to '+name+' is visible in WhatsApp. Delivery is not confirmed.' if status=='submitted' else 'WhatsApp shows your message to '+name+' as '+status+'.'
