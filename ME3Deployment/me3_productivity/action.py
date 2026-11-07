"""Onit proposes local work; host exact review and confirmation own mutations."""
import re
from me3_assistant.intent import ClarificationNeeded
from me3_assistant.reminder_server import exact_json_integer
from .store import digest,identifier,revision,validate_fields,READ_SOURCE

class ProductivityClarification(ClarificationNeeded):
 def __init__(self):
  self.code='productivity_details_required'
  self.reply='Please tell me which task, calendar entry or note you mean and what you would like changed. For appointments, give an unambiguous date, start/end time and timezone. Nothing has been changed.'
  ValueError.__init__(self,self.code)

def edit_data(args):
 data={k:args[k] for k in ('title','body','due_date','due_at','start_at','end_at','timezone') if args.get(k) is not None}
 if args.get('clear_due'):data['clear_due']=True
 return data

class ProductivityControl:
 budget_role='productivity_control'
 def __init__(self,registry,spec):
  self.registry=registry;self.spec=spec;self.server=spec['server']
  names={'prepare_productivity_change','commit_productivity_change','read_productivity_operation'}
  if any((self.server,n) not in registry.schemas for n in names) or names&set(registry.sessions[self.server][1]['read_tools']):raise ValueError('private_productivity_lifecycle_required')
 def declaration(self):
  return dict(name=self.spec['name'],description='Propose persistent Pi-local task/calendar/note work. Interpret natural requests; compose requested note text yourself, but draft-only requests must not save. Every mutation gets exact target/content/date review and separate confirmation. kind task: create/update/complete/reopen/cancel; event: create/update/cancel; note: create/update/archive. Creation needs title, notes also body, events explicit offset-bearing start_at/end_at and IANA timezone (configured default Asia/Manila; use productivity_context for current date/offset and clarify ambiguity). Task may have date-only due_date or exact due_at, or no due; clear_due removes it. Edits/state changes require exact target_id and current expected_revision from a read. Only supply intended edits; body empty string clears details. No deletes, external accounts/invitations or alarms; use the separate reminder tool for notifications.',parameters=self.registry.exposed_schema(self.server,'prepare_productivity_change'))
 def validate_request(self,request,g):
  c=getattr(self.registry,'communication_context',None)
  if not c or c.get('generation')!=g or c.get('request')!=request:raise ValueError('current_owner_productivity_request_required')
 async def prepare(self,args,g,operation_id):
  self.validate_request(self.registry.communication_context['request'],g)
  args=dict(args)
  if 'expected_revision' in args:args['expected_revision']=exact_json_integer(args['expected_revision'])
  try:
   validate_fields(args.get('kind'),args.get('command'),edit_data(args))
   if args.get('command')=='create':
    if args.get('target_id') is not None or args.get('expected_revision') is not None:raise ValueError('new_record_has_no_target')
   else:identifier(args.get('target_id'));revision(args.get('expected_revision'))
  except (ValueError,TypeError):raise ProductivityClarification() from None
  data=await self.registry.rpc(self.server,'prepare_productivity_change',g,dict(args,operation_id=operation_id));i=data.get('intent',{});after=i.get('after',{})
  if (data.get('operation_id')!=operation_id or data.get('expected_digest')!=digest(i)
      or i.get('kind')!=args['kind'] or i.get('command')!=args['command'] or after.get('kind')!=args['kind']
      or i.get('target_id')!=(operation_id if args['command']=='create' else args['target_id'])
      or i.get('expected_revision')!=(None if args['command']=='create' else args['expected_revision'])
      or after.get('revision')!=(1 if args['command']=='create' else args['expected_revision']+1)
      or not isinstance(data.get('token'),str) or not re.fullmatch('[0-9a-f]{64}',data['token'])):raise ValueError('exact_productivity_proposal_binding_required')
  noun='calendar entry' if i['kind']=='event' else i['kind']
  description=f"I'll {dict(create='create',update='update',complete='mark',reopen='reopen',cancel='cancel',archive='archive')[args['command']]} your {noun} \"{after['title']}\""
  if args['command']=='complete':description+=' as complete'
  for field,label in [('due_date','Due date'),('due_at','Due time'),('start_at','Starts'),('end_at','Ends'),('timezone','Timezone')]:
   if after.get(field) is not None:description+=f". {label}: {after[field]}"
  if i['kind']=='task' and after.get('due_date') is None and after.get('due_at') is None:description+='. No due date or time'
  description+=('. Details:\n'+after['body']) if after.get('body') else '. The details will be empty'
  if i['kind']=='event':description+='. Local calendar only; no invitation or reminder'
  return data,data['expected_digest'],description
 async def execute(self,p,g):return await self.registry.rpc(self.server,'commit_productivity_change',g,dict(operation_id=p['operation_id'],token=p['proposal']['token'],confirmation='confirm'))
 async def verify(self,p,g):
  data=await self.registry.rpc(self.server,'read_productivity_operation',g,dict(operation_id=p['operation_id']))
  if data.get('row','present') is None:return False
  if (data.get('verification_source')!=READ_SOURCE or data.get('operation_id')!=p['operation_id']
      or data.get('expected_digest')!=p['expected_digest'] or data.get('intent_digest')!=p['expected_digest']
      or type(data.get('applied_revision')) is not int or data['applied_revision']<1
      or type(data.get('current_revision')) is not int or data['current_revision']<data['applied_revision']
      or data.get('superseded')!=(data['current_revision']>data['applied_revision'])
      or not all(isinstance(data.get(k),str) and re.fullmatch('[0-9a-f]{64}',data[k]) for k in ('applied_after_digest','current_after_digest'))):raise ValueError('independent_productivity_readback_required')
  if not data['superseded'] and data['current_after_digest']!=data['applied_after_digest']:raise ValueError('productivity_current_state_mismatch')
  i=p.get('proposal',{}).get('intent')
  if i and (data.get('target_id')!=i['target_id'] or data['applied_revision']!=i['after']['revision'] or data['applied_after_digest']!=digest(i['after'])):raise ValueError('productivity_applied_snapshot_mismatch')
  return True
 def success_message(self,p):
  i=p['proposal']['intent'];row=i['after']
  noun='calendar entry' if i['kind']=='event' else i['kind']
  result=f"I've {dict(create='created',update='updated',complete='marked',reopen='reopened',cancel='canceled',archive='archived')[i['command']]} your {noun} \"{row['title']}\""
  if i['command']=='complete':result+=' as complete'
  return result+"."+(' It is saved in your local calendar; no invitation or reminder was set.' if i['kind']=='event' else '')
