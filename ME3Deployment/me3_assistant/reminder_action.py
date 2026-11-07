"""Model may propose; direct human confirmation owns scheduling and cancellation."""
import re
from datetime import datetime
from me3_assistant.reminders import digest

def spoken_time(value):
    """Speak the proposed instant explicitly, without reading ISO punctuation."""
    dt=datetime.fromisoformat(value.replace('Z','+00:00'))
    if dt.tzinfo is None or dt.utcoffset() is None:raise ValueError('timezone_offset_required')
    seconds=int(dt.utcoffset().total_seconds());magnitude=abs(seconds)
    zone='UTC' if not seconds else f"UTC {'plus' if seconds>0 else 'minus'} {magnitude//3600} hours"+(f' {magnitude%3600//60} minutes' if magnitude%3600//60 else '')+(f' {magnitude%60} seconds' if magnitude%60 else '')
    clock=f"{dt.hour%12 or 12}:{dt.minute:02d} {'AM' if dt.hour<12 else 'PM'}"+(f' and {dt.second} seconds' if dt.second else '')
    return f'{dt.day} {dt.strftime("%B %Y")} at {clock} {zone}'

class ReminderControl:
    budget_role='reminder_control'
    def __init__(self,registry,spec):
        self.registry=registry;self.spec=spec;self.server=spec['server'];names={'prepare_reminder','commit_reminder','read_reminder_operation'}
        if any((self.server,n) not in registry.schemas for n in names):raise ValueError('reminder_lifecycle_missing')
        if names&set(registry.sessions[self.server][1]['read_tools']):raise ValueError('reminder_lifecycle_exposed')
    def declaration(self):
        return {'name':self.spec['name'],'description':'Propose one-shot Pi-local alarm/reminder or cancellation. Ask for missing label/time and ambiguous timezone. Use delay_seconds for relative requests; due_at requires an explicit offset. GUI server must stay running. Visible GUI alert and one fixed local chime through an already active speaker-AEC voice mixer; idle/text/call-held alerts remain visual. A chime attempt is not proof the user heard it. This proposal never executes: direct human confirmation and reminder allowance required. No calendar, recurring/wake-from-off or generated speech notifications.','parameters':self.registry.exposed_schema(self.server,'prepare_reminder')}
    async def prepare(self,args,g,operation_id):
        data=await self.registry.rpc(self.server,'prepare_reminder',g,{**args,'operation_id':operation_id});intent=data['intent']
        if data.get('operation_id')!=operation_id or data.get('expected_digest')!=digest(intent) or not re.fullmatch('[0-9a-f]{32}',data.get('token','')):raise ValueError('reminder_proposal_mismatch')
        text=(f"Schedule {intent['kind']} {intent['label']} for {spoken_time(intent['display_time'])}" if intent['command']=='schedule' else f"Cancel pending {intent['kind']} {intent['label']} at {spoken_time(intent['display_time'])}")
        return data,digest(intent),text+'; the ME3 GUI server must remain running'
    async def execute(self,p,g):return await self.registry.rpc(self.server,'commit_reminder',g,{'operation_id':p['operation_id'],'token':p['proposal']['token'],'confirmation':'confirm'})
    async def verify(self,p,g):
        data=await self.registry.rpc(self.server,'read_reminder_operation',g,{'operation_id':p['operation_id']});row=data.get('row');intent=data.get('intent')
        if not row:return False
        if data.get('verification_source')!='fresh_reminder_sqlite_connection' or data.get('operation_id')!=p['operation_id'] or data.get('expected_digest')!=p['expected_digest'] or digest(intent)!=p['expected_digest']:raise ValueError('reminder_verification_mismatch')
        if intent['command']=='schedule':
            if row['id']!=p['operation_id'] or any(row[k]!=intent[k] for k in ('kind','label','due','display_time')):raise ValueError('reminder_state_mismatch')
            return row['state'] in {'scheduled','due','acknowledged'}
        return row['id']==intent['reminder_id'] and row['state']=='canceled'
    def success_message(self,p):
        i=p['proposal']['intent']
        return (f"Your {i['kind']} \"{i['label']}\" is set for {spoken_time(i['display_time'])}. Keep ME3 open for the alert." if i['command']=='schedule' else f"Canceled your {i['kind']} \"{i['label']}\". This does not undo any earlier alert.")
