"""Confirmation-owned actions with durable metadata, verification and no write retries."""
import asyncio,copy,hashlib,json,os,re,time,uuid
from pathlib import Path
from task_mcp.store import item_value,quantity_value,state_digest
from task_mcp.reasoner import prompt_delivered
from me3_assistant.intent import ClarificationNeeded

def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()

class Journal:
    def __init__(self,path):
        self.path=Path(path);self.records={}
        if self.path.exists():
            raw=self.path.read_bytes()
            if len(raw)>2_000_000:raise ValueError('action_journal_limit')
            for line in raw.decode('utf-8').splitlines():
                row=json.loads(line)
                if not re.fullmatch('[0-9a-f]{32}',row.get('operation_id','')) or row.get('state') not in {'PROPOSED','LOCAL_ENDED','HANDOFF_AUTHORIZED','DISPATCHED','VERIFIED','UNCERTAIN','CANCELED','NOT_DISPATCHED','SESSION_ENDED','OWNER_REPORTED_COMPLETE','RETIRED_UNCERTAIN','READ_UNVERIFIED','REJECTED_BEFORE_SEND'} or not re.fullmatch('[0-9a-f]{64}',row.get('expected_digest','')):raise ValueError('invalid_action_journal')
                owner=row.get('volatile_music_owner')
                if owner is not None and (row['action'] not in {'propose_music_control','propose_stream_control'} or not isinstance(owner,str) or not re.fullmatch('[0-9a-f]{32}',owner)):raise ValueError('invalid_music_journal_owner')
                if row['state']=='SESSION_ENDED' and row['action'] not in {'propose_music_control','propose_stream_control'}:raise ValueError('invalid_music_session_retirement')
                if row['state']=='HANDOFF_AUTHORIZED' and row['action']!='propose_whatsapp_call':raise ValueError('call_handoff_state_scope_required')
                if row['state']=='LOCAL_ENDED' and row['action']!='propose_whatsapp_call':raise ValueError('local_call_terminal_scope_required')
                prior=self.records.get(row['operation_id'])
                if row['state']=='READ_UNVERIFIED' and (row['action']!='propose_gmail_read' or not prior or prior['state'] not in {'DISPATCHED','UNCERTAIN'}):raise ValueError('prior_confirmed_gmail_read_required')
                if row['state']=='LOCAL_ENDED' and (not prior or prior['state'] not in {'HANDOFF_AUTHORIZED','DISPATCHED','UNCERTAIN','LOCAL_ENDED'}):raise ValueError('prior_owned_call_required_for_local_end')
                if row['state']=='HANDOFF_AUTHORIZED' and (not prior or prior['state']!='PROPOSED'):raise ValueError('call_handoff_requires_fresh_proposal')
                if prior and (prior['action']!=row['action'] or prior['expected_digest']!=row['expected_digest']):raise ValueError('action_journal_identity_changed')
                if prior and prior.get('volatile_music_owner')!=owner:raise ValueError('action_journal_owner_changed')
                if prior and prior['state']=='REJECTED_BEFORE_SEND':raise ValueError('rejected_operation_cannot_replay')
                if row['state']=='REJECTED_BEFORE_SEND':
                    from me3_whatsapp.contact_outbound import validate_pre_send_rejection
                    if row['action']!='propose_contact_message' or not prior or prior['state']!='UNCERTAIN':raise ValueError('prior_uncertain_contact_required')
                    validate_pre_send_rejection(self.path.parent.parent,row.get('pre_send_rejection'),row['operation_id'],row['expected_digest'])
                elif row.get('pre_send_rejection') is not None:raise ValueError('rejection_terminal_state_required')
                correction=row.get('owner_report_correction')
                if prior and prior['state']=='RETIRED_UNCERTAIN':raise ValueError('retired_unknown_operation_cannot_replay')
                if row['state']=='RETIRED_UNCERTAIN':
                    from me3_assistant.owner_reports import verify_retirement
                    if not prior or prior['state']!='UNCERTAIN':raise ValueError('prior_corrected_uncertainty_required')
                    verify_retirement(self.path.parent.parent if self.path.parent.name=='data' else self.path.parent,row.get('uncertain_retirement'),prior.get('owner_report_correction'),row['operation_id'],row['action'],row['expected_digest'])
                elif row.get('uncertain_retirement') is not None:raise ValueError('retirement_state_required')
                if prior and prior['state']=='OWNER_REPORTED_COMPLETE' and row!=prior:
                    from me3_assistant.owner_reports import verify_correction
                    if row['state']!='UNCERTAIN':raise ValueError('owner_reported_operation_cannot_replay')
                    verify_correction(self.path.parent.parent if self.path.parent.name=='data' else self.path.parent,correction,prior.get('owner_report'),row['operation_id'],row['action'],row['expected_digest'])
                elif correction is not None:
                    if not prior or correction!=prior.get('owner_report_correction') or row['state'] not in {'UNCERTAIN','VERIFIED','RETIRED_UNCERTAIN'}:raise ValueError('owner_correction_cannot_replay')
                if prior and prior.get('owner_report_correction') and (correction!=prior['owner_report_correction'] or row['state'] not in {'UNCERTAIN','VERIFIED','RETIRED_UNCERTAIN'}):raise ValueError('owner_correction_cannot_replay')
                if row['state']=='OWNER_REPORTED_COMPLETE':
                    from me3_assistant.owner_reports import verify
                    if not prior or prior['state'] not in {'UNCERTAIN','DISPATCHED','OWNER_REPORTED_COMPLETE'}:raise ValueError('owner_report_requires_uncertain_operation')
                    verify(self.path.parent.parent if self.path.parent.name=='data' else self.path.parent,row.get('owner_report'),row['operation_id'],row['action'],row['expected_digest'])
                self.records[row['operation_id']]=row
    def record(self,operation_id,action,state,expected_digest,*,volatile_music_owner=None,owner_report=None,owner_report_correction=None,uncertain_retirement=None,pre_send_rejection=None):
        row={'operation_id':operation_id,'action':action,'state':state,'expected_digest':expected_digest}
        prior=self.records.get(operation_id)
        if prior and prior['state']=='REJECTED_BEFORE_SEND':raise ValueError('rejected_operation_cannot_replay')
        if state=='REJECTED_BEFORE_SEND':
            from me3_whatsapp.contact_outbound import validate_pre_send_rejection
            if action!='propose_contact_message' or not prior or prior['state']!='UNCERTAIN' or prior.get('action')!=action or prior.get('expected_digest')!=expected_digest:raise ValueError('prior_uncertain_contact_required')
            validate_pre_send_rejection(self.path.parent.parent,pre_send_rejection,operation_id,expected_digest);row['pre_send_rejection']=copy.deepcopy(pre_send_rejection)
        elif pre_send_rejection is not None:raise ValueError('rejection_terminal_state_required')
        if state=='READ_UNVERIFIED' and (action!='propose_gmail_read' or not prior or prior['state'] not in {'DISPATCHED','UNCERTAIN'}):raise ValueError('prior_confirmed_gmail_read_required')
        if state=='LOCAL_ENDED' and (action!='propose_whatsapp_call' or not prior or prior['state'] not in {'HANDOFF_AUTHORIZED','DISPATCHED','UNCERTAIN','LOCAL_ENDED'}):raise ValueError('prior_owned_call_required_for_local_end')
        if state=='HANDOFF_AUTHORIZED' and (action!='propose_whatsapp_call' or not prior or prior['state']!='PROPOSED'):raise ValueError('call_handoff_requires_fresh_call_proposal')
        if prior and prior['state']=='RETIRED_UNCERTAIN':raise ValueError('retired_unknown_operation_cannot_replay')
        if state=='RETIRED_UNCERTAIN':
            from me3_assistant.owner_reports import verify_retirement
            if not prior or prior['state']!='UNCERTAIN':raise ValueError('prior_corrected_uncertainty_required')
            verify_retirement(self.path.parent.parent if self.path.parent.name=='data' else self.path.parent,uncertain_retirement,prior.get('owner_report_correction'),operation_id,action,expected_digest);row['uncertain_retirement']=uncertain_retirement
        elif uncertain_retirement is not None:raise ValueError('retirement_state_required')
        if prior:
            if prior['action']!=action or prior['expected_digest']!=expected_digest:raise ValueError('action_journal_identity_changed')
            if volatile_music_owner is not None and volatile_music_owner!=prior.get('volatile_music_owner'):raise ValueError('action_journal_owner_changed')
            volatile_music_owner=prior.get('volatile_music_owner')
        if volatile_music_owner is not None:
            if action not in {'propose_music_control','propose_stream_control'} or not re.fullmatch('[0-9a-f]{32}',volatile_music_owner):raise ValueError('invalid_music_journal_owner')
            row['volatile_music_owner']=volatile_music_owner
        if state=='SESSION_ENDED' and (action not in {'propose_music_control','propose_stream_control'} or not prior or prior['state'] not in {'UNCERTAIN','DISPATCHED'}):raise ValueError('invalid_music_session_retirement')
        if prior and prior['state']=='OWNER_REPORTED_COMPLETE':
            from me3_assistant.owner_reports import verify_correction
            if state!='UNCERTAIN':raise ValueError('owner_reported_operation_cannot_replay')
            verify_correction(self.path.parent.parent if self.path.parent.name=='data' else self.path.parent,owner_report_correction,prior.get('owner_report'),operation_id,action,expected_digest)
            row['owner_report_correction']=owner_report_correction
        elif prior and prior.get('owner_report_correction'):
            if state not in {'UNCERTAIN','VERIFIED','RETIRED_UNCERTAIN'} or owner_report_correction is not None and owner_report_correction!=prior['owner_report_correction']:raise ValueError('owner_correction_cannot_replay')
            row['owner_report_correction']=prior['owner_report_correction']
        elif owner_report_correction is not None:raise ValueError('prior_owner_report_required')
        if state=='OWNER_REPORTED_COMPLETE':
            from me3_assistant.owner_reports import verify
            if not prior or prior['state'] not in {'UNCERTAIN','DISPATCHED'}:raise ValueError('owner_report_requires_uncertain_operation')
            verify(self.path.parent.parent if self.path.parent.name=='data' else self.path.parent,owner_report,operation_id,action,expected_digest);row['owner_report']=owner_report
        elif owner_report is not None:raise ValueError('owner_report_state_required')
        self.path.parent.mkdir(parents=True,exist_ok=True)
        if self.path.exists() and self.path.stat().st_size>2_000_000:raise ValueError('action_journal_limit')
        with self.path.open('a',encoding='utf-8') as f:
            f.write(json.dumps(row,separators=(',',':'))+'\n');f.flush();os.fsync(f.fileno())
        self.records[operation_id]=row
        return row
    def unresolved(self):return [x for x in self.records.values() if x['state'] in {'HANDOFF_AUTHORIZED','DISPATCHED','UNCERTAIN'}]
    def end_music_owner(self,owner,*,actions=('propose_music_control',)):
        if not isinstance(owner,str) or not re.fullmatch('[0-9a-f]{32}',owner):raise ValueError('invalid_music_journal_owner')
        if not set(actions)<={'propose_music_control','propose_stream_control'}:raise ValueError('invalid_volatile_action_scope')
        rows=[r for r in self.unresolved() if r.get('volatile_music_owner')==owner and r['action'] in actions]
        for r in rows:self.record(r['operation_id'],r['action'],'SESSION_ENDED',r['expected_digest'])
        return len(rows)

class ShoppingItem:
    budget_role='commit_item'
    def __init__(self,registry,spec):
        self.registry=registry;self.spec=spec;self.server=spec['server']
        if any((self.server,name) not in registry.schemas for name in ['prepare_item','commit_item','read_item']):raise ValueError('action_lifecycle_tools_missing')
        if any(name in registry.sessions[self.server][1]['read_tools'] for name in ['prepare_item','commit_item','read_item']):raise ValueError('action_lifecycle_tool_exposed')
    def declaration(self):
        return {'name':self.spec['name'],'description':'Propose a Pi shopping-list addition. Ask for missing item or quantity. This never saves; direct human confirmation and authorization are required.','parameters':self.registry.exposed_schema(self.server,'prepare_item')}
    async def prepare(self,args,g,operation_id):
        clean={'item':item_value(args['item']),'quantity':quantity_value(args['quantity'])}
        data=await self.registry.rpc(self.server,'prepare_item',g,{**clean,'operation_id':operation_id})
        if data.get('operation_id')!=operation_id or any(data.get(k)!=v for k,v in clean.items()) or not re.fullmatch('[0-9a-f]{32}',data.get('token','')):raise ValueError('action_proposal_mismatch')
        return data,digest(clean),f"Add {clean['quantity']} {clean['item']} to your Pi shopping list"
    async def execute(self,pending,g):
        return await self.registry.rpc(self.server,'commit_item',g,{'operation_id':pending['operation_id'],'token':pending['proposal']['token'],'confirmation':'confirm'})
    def success_message(self,pending):
        p=pending['proposal']
        return f"Added {p['quantity']} {p['item']} to your Pi shopping list."
    async def verify(self,record,g):
        data=await self.registry.rpc(self.server,'read_item',g,{'operation_id':record['operation_id']})
        row=data.get('row')
        if not isinstance(row,dict):return False
        expected=digest({'item':item_value(row['item']),'quantity':quantity_value(row['quantity'])})
        if data.get('operation_id')!=record['operation_id'] or row.get('operation_id')!=record['operation_id'] or expected!=record['expected_digest'] or data.get('state_digest')!=state_digest(row) or data.get('verification_source')!='fresh_readonly_sqlite_connection':raise ValueError('action_verification_mismatch')
        return True

from me3_assistant.youtube_action import YouTubeControl
from me3_assistant.music_action import MusicControl
from me3_assistant.reminder_action import ReminderControl
from me3_productivity.action import ProductivityControl
from me3_google_calendar.action import GoogleCalendarControl
from me3_gmail.action import GmailSend
from me3_gmail.read_action import GmailRead
from me3_stream.action import SoundCloudControl
from me3_whatsapp.action import WhatsAppMessage
from me3_whatsapp.contact_message_action import ContactMessage
from me3_whatsapp.window_action import WhatsAppWindow
from me3_whatsapp.call_action import WhatsAppCall
ADAPTERS={'google_calendar_control':GoogleCalendarControl,'gmail_read':GmailRead,'gmail_send':GmailSend,'productivity_control':ProductivityControl,'whatsapp_call':WhatsAppCall,'whatsapp_contact_message':ContactMessage,'whatsapp_window':WhatsAppWindow,'whatsapp_message':WhatsAppMessage,'soundcloud_control':SoundCloudControl,'shopping_item':ShoppingItem,'music_control':MusicControl,'youtube_control':YouTubeControl,'reminder_control':ReminderControl}

def volume_meaning_clarification(request,args):
    # A narrow parameter-consistency guard; never rewrite ASR or choose a tool.
    # Only a host-bound current request and an actual volume action are examined.
    if not isinstance(request,str) or args.get('command')!='volume':return None
    values=re.findall(r'(?<![\w.])([+-]?\d+(?:\.\d+)?)\s*(?:%|percent\b)',request,re.I)
    outside=next((n for n in values if not 0<=float(n)<=100),None)
    if outside is None:return None
    return 'The transcript says '+outside+' percent. Player volume supports 0 to 100 percent. Did you mean '+str(args['volume'])+' percent? Say confirm, or tell me the volume you want. No volume change has started.'

class Actions:
    def __init__(self,registry,specs,budget,journal,events,current,clock=time.monotonic,allowed=None):
        self.registry=registry;self.journal=journal;self.events=events;self.current=current;self.clock=clock;self.pending=None;self._uncertain_cleanup_permit=None
        from me3_assistant.local_actions import LocalActionBudget
        self.budget=LocalActionBudget(budget,getattr(registry,'session_grant',{}),getattr(registry,'local_action_directory',journal.path.parent/'local-action-budgets'))
        self.adapters={s['name']:ADAPTERS[s['adapter']](registry,s) for s in specs}
        from me3_assistant.standing_music import authorized,StandingMusicBudget
        if authorized(getattr(registry,'session_grant',{})):
            path=getattr(registry,'standing_music_counter',Path(registry.session_grant['operation_state_path']).parent/'standing-music-actions.json')
            self.budget=StandingMusicBudget(budget,registry.session_grant,path)
        from me3_whatsapp.budget import authorized as messages_authorized,StandingMessageBudget
        if messages_authorized(getattr(registry,'session_grant',{})):
            path=getattr(registry,'standing_message_counter',Path(registry.session_grant['operation_state_path']).parent/'standing-message-actions.json')
            self.budget=StandingMessageBudget(self.budget,registry.session_grant,path)
        from me3_whatsapp.window_budget import authorized as window_authorized,StandingWindowBudget
        if window_authorized(getattr(registry,'session_grant',{})):
            path=getattr(registry,'standing_window_counter',Path(registry.session_grant['operation_state_path']).parent/'standing-window-actions.json')
            self.budget=StandingWindowBudget(self.budget,registry.session_grant,path)
        from me3_whatsapp.call_budget import authorized as calls_authorized,StandingCallBudget
        if calls_authorized(getattr(registry,'session_grant',{})):
            path=getattr(registry,'standing_call_counter',Path(registry.session_grant['operation_state_path']).parent/'standing-call-actions.json')
            self.budget=StandingCallBudget(self.budget,registry.session_grant,path)
        from me3_productivity.budget import authorized as productivity_authorized,StandingProductivityBudget
        if productivity_authorized(getattr(registry,'session_grant',{})):
            path=getattr(registry,'standing_productivity_counter',Path(registry.session_grant['operation_state_path']).parent/'standing-productivity-actions.json')
            self.budget=StandingProductivityBudget(self.budget,registry.session_grant,path)
        from me3_gmail.budget import authorized as gmail_authorized,StandingGmailBudget
        if gmail_authorized(getattr(registry,'session_grant',{})):
            path=getattr(registry,'standing_gmail_counter',Path(registry.session_grant['operation_state_path']).parent/'standing-gmail-actions.json')
            self.budget=StandingGmailBudget(self.budget,registry.session_grant,path)
        from me3_gmail.read_budget import authorized as gmail_read_authorized,StandingGmailReadBudget
        if gmail_read_authorized(getattr(registry,'session_grant',{})):
            path=getattr(registry,'standing_gmail_read_counter',Path(registry.session_grant['operation_state_path']).parent/'standing-gmail-read-actions.json')
            self.budget=StandingGmailReadBudget(self.budget,registry.session_grant,path)
        from me3_google_calendar.budget import authorized as calendar_authorized,StandingCalendarBudget
        if calendar_authorized(getattr(registry,'session_grant',{})):
            path=getattr(registry,'standing_calendar_counter',Path(registry.session_grant['operation_state_path']).parent/'standing-google-calendar-actions.json')
            self.budget=StandingCalendarBudget(self.budget,registry.session_grant,path)
            registry.google_calendar_budget=self.budget
        self.last_handoff=None
        self.last_completed=None;self.request_text=None;self.preparation_clarification=None
        self.allowed=set(self.adapters) if allowed is None else set(allowed)
    def declarations(self):return [a.declaration() for name,a in self.adapters.items() if name in self.allowed and getattr(a,'available',True)]
    def cancel(self):
        if self.pending:
            p=self.pending;self.journal.record(p['operation_id'],p['action'],'CANCELED',p['expected_digest']);self.pending=None
            self.events.emit('assistant_action_canceled',operation_id=p['operation_id'],dispatched=False)
    async def propose(self,name,args,g):
        self.cancel();self.last_completed=None;self.preparation_clarification=None;adapter=self.adapters[name]
        if name not in self.allowed:return 'That action is not authorized in this session. I can use the available read-only tools.'
        if self.journal.unresolved():
            permit=getattr(self,'_uncertain_cleanup_permit',None)
            if permit is not None:
                from onit_candidate.gateway import _WindowCleanupPermit
                cleanup=type(permit) is _WindowCleanupPermit and permit.consume(self,name,args,g) is True
            else:cleanup=False
            if not cleanup:return 'An earlier action has an unverified outcome. Ask me to check its status before another action.'
        if not self.budget.permitted(adapter.budget_role):return 'Saving this action is not authorized in the current session. I can still use the available read-only tools.'
        op=uuid.uuid4().hex
        rpc_before=getattr(self.registry,'calls',None);journal_before=copy.deepcopy(self.journal.records)
        try:
            validate_request=getattr(adapter,'validate_request',None)
            if validate_request:validate_request(self.request_text,g)
            ensure_output=getattr(self.registry,'ensure_music_output',None)
            if name=='propose_music_control' and adapter.budget_role=='music_control' and args.get('command')!='save_playlist' and ensure_output:
                await ensure_output()
                if not self.current(g):return ''
            proposal,expected,description=await adapter.prepare(args,g,op)
        except asyncio.CancelledError:raise
        except ClarificationNeeded as exc:
            self.preparation_clarification=dict(reason=exc.code,reply=exc.reply)
            rpc_after=getattr(self.registry,'calls',None)
            # Private host evidence for a prerequisite rejected strictly before
            # any RPC or journal mutation. A backend failure is not this case.
            if (type(rpc_before) is int and type(rpc_after) is int and rpc_before==rpc_after
                and self.journal.records==journal_before and op not in self.journal.records
                and self.pending is None and self.last_completed is None):
                self.preparation_clarification['no_rpc_proof']=dict(action=name,generation=g,operation_id=op,rpc_calls_before=rpc_before,rpc_calls_after=rpc_after,journal_operations=len(journal_before),journal_unchanged=True,no_pending=True,no_completion=True)
            self.events.emit('assistant_action_needs_clarification',generation=g,reason=exc.code,execution_started=False)
            return exc.reply
        except Exception:return 'I could not prepare that action. No execution was started.'
        if not self.current(g):return ''
        clarification=bool(getattr(adapter,'requires_clarification',lambda p:False)(proposal))
        range_prompt=volume_meaning_clarification(self.request_text,args) if adapter.budget_role=='music_control' else None
        prompt=adapter.clarification_prompt(proposal) if clarification else range_prompt or description+'. Say confirm to proceed, or cancel.'
        clarification=clarification or range_prompt is not None
        # Playback/queue/volume effects end with this music MCP owner. Saving a
        # playlist is persistent and must retain the ordinary uncertain guard.
        owner=getattr(self.registry,'music_owner',None) if name in {'propose_music_control','propose_stream_control'} and adapter.budget_role=='music_control' and args.get('command')!='save_playlist' else None
        self.journal.record(op,name,'PROPOSED',expected,**({'volatile_music_owner':owner} if owner else {}))
        self.pending={'phase':'confirm','operation_id':op,'action':name,'proposal':proposal,'expected_digest':expected,'prompt':prompt,'meaning_clarification':clarification,'expires':self.clock()+120}
        from me3_assistant.standing_music import authorized
        if not clarification and adapter.budget_role=='music_control' and authorized(getattr(self.registry,'session_grant',{})):
            return await self.execute_pending(g,source='owner_standing_local_music')
        if adapter.budget_role=='window_control':
            return await self.execute_pending(g,source='direct_current_user_window_close')
        self.events.emit('assistant_confirmation_requested',generation=g,operation_id=op,action=name)
        return prompt
    async def confirm(self,history,g,*,clarify_only=False):
        p=self.pending
        if p is None:return 'There is no pending action to confirm. I will not repeat a completed action.'
        if self.clock()>p['expires']:
            self.cancel();return 'That proposal expired. Please make a fresh request.'
        if not self.current(g):return ''
        if clarify_only:
            self.events.emit('assistant_confirmation_uncertain',generation=g,operation_id=p['operation_id'],execution_started=False)
            return p['prompt']
        if not prompt_delivered(history,p['prompt']):return p['prompt']
        return await self.execute_pending(g,source='direct_current_user_after_completed_prompt')
    async def execute_pending(self,g,*,source):
        p=self.pending
        if p is None or not self.current(g):return ''
        if source=='owner_standing_local_music':
            from me3_assistant.standing_music import authorized
            if not authorized(getattr(self.registry,'session_grant',{})) or self.adapters[p['action']].budget_role!='music_control':raise ValueError('standing_action_scope_invalid')
        adapter=self.adapters[p['action']]
        if source=='direct_current_user_window_close':
            from me3_whatsapp.window_budget import authorized as window_authorized
            if adapter.budget_role!='window_control' or not window_authorized(getattr(self.registry,'session_grant',{})):raise ValueError('window_action_not_authorized')
            adapter.validate_request(self.request_text,g)
        if source=='owner_standing_local_music' and (p.get('meaning_clarification') or getattr(adapter,'requires_clarification',lambda p:False)(p['proposal'])):return p['prompt']
        if not self.budget.permitted(adapter.budget_role):
            self.cancel();return 'That action is not authorized in this session. No execution was started.'
        if adapter.budget_role=='call_start':
            if p['action']!='propose_whatsapp_call' or source!='direct_current_user_after_completed_prompt':raise ValueError('separate_direct_call_confirmation_required')
            self.pending=None;self.last_handoff=None
            self.journal.record(p['operation_id'],p['action'],'HANDOFF_AUTHORIZED',p['expected_digest'])
            try:
                self.budget.reserve('call_start')
                p['_confirmation_source']=source;p['_confirmed_generation']=g
                queued=await adapter.handoff(p,g)
                if queued.get('operation_id')!=p['operation_id'] or queued.get('state')!='HANDOFF_AUTHORIZED' or queued.get('execution_started') is not False or queued.get('verified') is not False:raise ValueError('unverified_handoff_receipt_required')
                self.last_handoff=queued
                self.events.emit('assistant_human_confirmation',generation=g,operation_id=p['operation_id'],source=source)
                self.events.emit('assistant_call_handoff_authorized',generation=g,operation_id=p['operation_id'],dial_started=False,verified=False)
                return 'The call is confirmed. ME3 listening will pause while WhatsApp uses the microphone and speakers.'
            except BaseException:
                # Queueing never means a dial or an observed outcome. The GUI
                # owns cleanup/reconciliation; never retry or claim completion.
                self.journal.record(p['operation_id'],p['action'],'UNCERTAIN',p['expected_digest']);raise
        self.pending=None;dispatched=False
        try:
            # Persist recovery identity before spending/dispatch. No token or private arguments are logged.
            self.journal.record(p['operation_id'],p['action'],'DISPATCHED',p['expected_digest'])
            self.budget.reserve(adapter.budget_role)
            if not self.current(g):
                self.journal.record(p['operation_id'],p['action'],'CANCELED',p['expected_digest']);return ''
            dispatched=True;self.events.emit('assistant_standing_music_authorized' if source=='owner_standing_local_music' else 'assistant_current_user_window_command' if source=='direct_current_user_window_close' else 'assistant_human_confirmation',generation=g,operation_id=p['operation_id'],source=source)
            p['_confirmation_source']=source;p['_confirmed_generation']=g
            await adapter.execute(p,g)
            if not self.current(g):raise asyncio.CancelledError()
            if not await adapter.verify(p,g):raise ValueError('action_not_verified')
            self.journal.record(p['operation_id'],p['action'],'VERIFIED',p['expected_digest'])
            self.events.emit('assistant_action_verified',generation=g,operation_id=p['operation_id'],action=p['action'])
            self.last_completed={'operation_id':p['operation_id'],'action':p['action'],'generation':g,'standing_routine':source=='owner_standing_local_music'}
            report=getattr(adapter,'success_message',lambda p:'The action completed and I independently checked its saved state.')(p)
            return report if self.current(g) else ''
        except asyncio.CancelledError:
            self.journal.record(p['operation_id'],p['action'],'READ_UNVERIFIED' if dispatched and adapter.budget_role=='gmail_read' else 'UNCERTAIN' if dispatched else 'CANCELED',p['expected_digest']);raise
        except Exception:
            self.journal.record(p['operation_id'],p['action'],'READ_UNVERIFIED' if dispatched and adapter.budget_role=='gmail_read' else 'UNCERTAIN' if dispatched else 'NOT_DISPATCHED',p['expected_digest'])
            if dispatched and adapter.budget_role=='gmail_read':return 'I could not complete the confirmed email read. I will not repeat it automatically. You can still talk with me.'
            return 'I could not verify the action outcome. I will not repeat it automatically; ask me to check its status.' if dispatched else 'The action could not start within its authorization. No execution was started.'
    async def status(self,g):
        if not self.journal.records:return {'status':'no_previous_action'}
        record=list(self.journal.records.values())[-1]
        if record['state']=='REJECTED_BEFORE_SEND':return {'status':'rejected_before_send','send_started':False,'write_retried':False,'old_operation_reusable':False}
        if record['state']=='LOCAL_ENDED':return {'status':'call_closed_locally','remote_end_confirmed':False,'call_connected_not_established':True,'write_retried':False}
        if record['state']=='HANDOFF_AUTHORIZED':return {'status':'call_handoff_pending','verified':False,'execution_started':False,'write_retried':False}
        if record['state']=='SESSION_ENDED':return {'status':'previous_music_session_ended','original_execution_outcome':'not_verified','write_retried':False,'persistent_actions_undone':False}
        if record['state'] in {'CANCELED','NOT_DISPATCHED','PROPOSED'}:return {'status':record['state'].lower(),'execution_started':False}
        adapter=self.adapters.get(record['action'])
        if adapter is None:return {'status':'unverified','reason':'adapter_not_available','write_retried':False}
        try:verified=await adapter.verify(record,g)
        except asyncio.CancelledError:raise
        except Exception:verified=False
        if verified:self.journal.record(record['operation_id'],record['action'],'VERIFIED',record['expected_digest'])
        return {'status':'independently_verified' if verified else 'unverified','write_retried':False}
