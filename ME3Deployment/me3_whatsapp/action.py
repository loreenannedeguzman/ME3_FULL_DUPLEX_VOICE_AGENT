"""Voice/text self-message adapter with completed host review and verification."""
import hashlib,json,re
def fingerprint(intent):return hashlib.sha256(json.dumps(intent,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
def clean(recipient,text):
 if recipient!='self' or not isinstance(text,str) or not text.strip() or text!=text.strip() or len(text.encode('utf-8'))>2000 or any(ord(c)<32 and c not in '\n\t' for c in text):raise ValueError('exact_self_message_within_2000_UTF8_bytes_required')
 return dict(recipient='self',text=text)
class WhatsAppMessage:
 budget_role='message_send'
 def __init__(self,registry,spec):
  self.registry=registry;self.spec=spec;self.server=spec['server'];private={'prepare_self_message','commit_self_message','read_message_operation'}
  if any((self.server,n) not in registry.schemas for n in private):raise ValueError('whatsapp_private_lifecycle_missing')
  if private&set(registry.sessions[self.server][1]['read_tools']):raise ValueError('whatsapp_private_lifecycle_exposed_to_model')
 def declaration(self):
  return dict(name=self.spec['name'],description='Prepare an exact WhatsApp self-chat message (maximum2000UTF8bytes) for spoken/displayed review. Does not send. Completed exact-content review and direct human confirmation are required. No other contacts, inbox or calling.',parameters=dict(type='object',properties=dict(recipient=dict(type='string',enum=['self']),text=dict(type='string',minLength=1,maxLength=2000,description='Exact text, at most2000UTF8bytes; no leading/trailing whitespace.')),required=['recipient','text'],additionalProperties=False))
 async def prepare(self,args,g,operation_id):
  intent=clean(args['recipient'],args['text']);expected=fingerprint(intent);data=await self.registry.rpc(self.server,'prepare_self_message',g,dict(**intent,operation_id=operation_id))
  if data.get('operation_id')!=operation_id or data.get('intent')!=intent or data.get('expected_digest')!=expected or not re.fullmatch('[0-9a-f]{32}',data.get('token','')):raise ValueError('whatsapp_proposal_binding_mismatch')
  return data,expected,'Send this exact message to your WhatsApp self-chat: '+intent['text']
 async def execute(self,p,g):return await self.registry.rpc(self.server,'commit_self_message',g,dict(operation_id=p['operation_id'],token=p['proposal']['token'],confirmation='confirm'))
 async def verify(self,p,g):
  data=await self.registry.rpc(self.server,'read_message_operation',g,dict(operation_id=p['operation_id']));row=data.get('row') or {};observation=data.get('observation') or {}
  if row.get('operation_id')!=p['operation_id'] or row.get('expected_digest')!=p['expected_digest'] or fingerprint(row.get('intent'))!=p['expected_digest'] or row.get('state') not in {'SUBMITTED','VERIFIED','IMPORTED_OWNER_VERIFIED'}:return False
  if data.get('verification_source')!='fresh_scoped_whatsapp_ui' or observation.get('exact_outgoing_message') is not True or observation.get('recipient')!='self' or observation.get('text_sha256')!=hashlib.sha256(row['intent']['text'].encode()).hexdigest() or observation.get('status') not in {'submitted','sent','delivered','read'}:return False
  p['verified_message_status']=observation['status'];return True
 def success_message(self,p):
  status=p.get('verified_message_status')
  if status=='submitted':return 'Your message is visible in your WhatsApp self-chat. Delivery status is not available from this interface.'
  if status in {'sent','delivered','read'}:return 'WhatsApp shows your self-message as '+status+'.'
  raise ValueError('verified_message_status_required')
