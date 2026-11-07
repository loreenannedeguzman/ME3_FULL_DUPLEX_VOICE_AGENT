"""Host-only current-command window action; no message/call permission granted."""
import re
from .window import command_allowed
class WhatsAppWindow:
 budget_role='window_control'
 def __init__(self,registry,spec):
  self.registry=registry;self.spec=spec;self.server=spec['server'];self.request=None
  private={'prepare_window_close','commit_window_close','read_window_operation'}
  if any((self.server,n) not in registry.schemas for n in private) or private&set(registry.sessions[self.server][1]['read_tools']):raise ValueError('private_window_lifecycle_required')
 def declaration(self):return dict(name=self.spec['name'],description='Close the owned WhatsApp app window only when the CURRENT user explicitly asks to close it. Never closes ME3 or another app. Protects current drafts, visible active calls and unresolved dialogs. Does not stop conversation or send/call anyone.',parameters=dict(type='object',properties={},additionalProperties=False))
 def validate_request(self,request,g):
  if not command_allowed(request):raise ValueError('explicit_current_close_command_required')
  self.request=request
 async def prepare(self,args,g,op):
  if args or not command_allowed(self.request):raise ValueError('current_window_command_required')
  value=await self.registry.rpc(self.server,'prepare_window_close',g,dict(operation_id=op,current_user_request=self.request))
  if value.get('operation_id')!=op or not re.fullmatch('[0-9a-f]{32}',value.get('token','')) or not re.fullmatch('[0-9a-f]{64}',value.get('expected_digest','')) or value.get('will_stop_conversation') is not False:raise ValueError('window_proposal_binding_mismatch')
  return value,value['expected_digest'],'Close the owned WhatsApp window and keep this conversation available'
 async def execute(self,p,g):return await self.registry.rpc(self.server,'commit_window_close',g,dict(operation_id=p['operation_id'],token=p['proposal']['token']))
 async def verify(self,p,g):
  v=await self.registry.rpc(self.server,'read_window_operation',g,dict(operation_id=p['operation_id']))
  return v.get('operation_id')==p['operation_id'] and v.get('expected_digest')==p['expected_digest'] and v.get('state')=='VERIFIED' and v.get('target_closed') is True and v.get('verification_source')=='fresh_owned_target_list_or_owned_process_exit' and v.get('conversation_stop_requested') is False
 def success_message(self,p):return 'WhatsApp is closed. You can keep talking with me.'
