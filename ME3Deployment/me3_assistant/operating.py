"""Standing normal-use authority with monotonic accounting and rolling admission.

This profile never changes historical development ceilings or borrows benchmark
capacity. Every attempt still increments the original cumulative usage record.
"""
import asyncio, hashlib, json, math, time
from pathlib import Path
from benchmark_m6.storage import read, atomic
from speech_mcp.client import ProviderLimited

ROLES={'asr','reasoning','tts','commit_item'}
ACCOUNTING_LOCK_TIMEOUT=1.0  # Local lease acquisition only; no provider replay.

def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def authorize_operating(config,source,mode,backend=None,root=None):
    from task_mcp.client import ROOT
    from me3_assistant.backends import selected
    from me3_assistant.acoustic_echo import audio_mode
    root=Path(root or ROOT)
    resource=root/'config/operating_policy.json';policy=read(resource)
    path=root/'evidence/assistant/operating-authorization.json';authority=read(path)
    if authority.get('approved') is not True:raise ValueError('operating_policy_revoked')
    if mode not in {'normal','text'} or mode not in policy.get('modes',[]):raise ValueError('unsupported_operating_mode')
    if authority.get('source_revision')!=source['revision'] or authority.get('config_sha256')!=config['sha256'] or authority.get('resource_policy_sha256')!=digest(resource) or source['source_hashes'].get('config/operating_policy.json')!=digest(resource):raise ValueError('operating_policy_binding_mismatch')
    if policy.get('runtime')!=config['runtime']:raise ValueError('operating_runtime_mismatch')
    if type(policy.get('workspace_access')) is not bool:raise ValueError('operating_actions_not_authorized')
    if policy['workspace_access']:
        workspace=[s for s in config['servers'] if s['id']=='workspace']
        from me3_assistant.config import WORKSPACE_GRANTED
        if (policy.get('workspace_scope')!='pi_artifacts_only' or len(workspace)!=1
            or workspace[0]['module']!='me3_assistant.workspace_server' or workspace[0]['args']!=[]
            or workspace[0]['identity']!='me3' or workspace[0]['role']!='utility'
            or set(workspace[0].get('workspace_tools',[]))!=WORKSPACE_GRANTED
            or set(workspace[0]['read_tools'])!={'workspace_capabilities','list_workspace_files','read_workspace_file'}):
            raise ValueError('dedicated_Pi_workspace_authority_required')
    if policy.get('routine_local_music') is True:
        expected=['propose_music_control','propose_youtube_control'] if policy.get('routine_youtube_music') is True else ['propose_music_control']
        if policy.get('routine_soundcloud_music') is True:
            expected.append('propose_stream_control')
            sc=[a for a in config.get('actions',[]) if a['name']=='propose_stream_control' and a['adapter']=='soundcloud_control']
            servers={s['id']:s for s in config.get('servers',[])}
            if len(sc)!=1 or servers.get(sc[0]['server'],{}).get('module')!='me3_stream.server':raise ValueError('operating_SoundCloud_not_authorized')
        if policy.get('routine_youtube_music') is True and not any(a['name']=='propose_youtube_control' and a['adapter']=='youtube_control' for a in config.get('actions',[])):raise ValueError('operating_youtube_not_authorized')
        if policy.get('routine_whatsapp_messages') is True:
            expected.append('propose_whatsapp_message')
            wa=[a for a in config.get('actions',[]) if a['name']=='propose_whatsapp_message' and a['adapter']=='whatsapp_message']
            servers={s['id']:s for s in config.get('servers',[])}
            if policy.get('whatsapp_recipient_scope')!='self' or len(wa)!=1 or servers.get(wa[0]['server'],{}).get('module')!='me3_whatsapp.server':raise ValueError('operating_WhatsApp_not_authorized')
        if policy.get('routine_whatsapp_contact_messages') is True:
            expected.append('propose_contact_message')
            wa_contact=[a for a in config.get('actions',[]) if a['name']=='propose_contact_message' and a['adapter']=='whatsapp_contact_message']
            if policy.get('routine_whatsapp_messages') is True or policy.get('whatsapp_recipient_scope')!='verified_contacts' or len(wa_contact)!=1 or servers.get(wa_contact[0]['server'],{}).get('module')!='me3_whatsapp.server' or policy.get('whatsapp_qualification_outbound_only')!=[dict(name='CONTACT_LABEL',phone='+12025550123')]:raise ValueError('named_WhatsApp_qualification_not_authorized')
        if policy.get('routine_whatsapp_window') is True:
            expected.append('propose_whatsapp_window')
            wa_window=[a for a in config.get('actions',[]) if a['name']=='propose_whatsapp_window' and a['adapter']=='whatsapp_window']
            if len(wa_window)!=1 or servers.get(wa_window[0]['server'],{}).get('module')!='me3_whatsapp.server':raise ValueError('window_action_not_authorized')
        if policy.get('routine_whatsapp_calls') is True:
            expected.append('propose_whatsapp_call')
            calls=[a for a in config.get('actions',[]) if a['name']=='propose_whatsapp_call' and a['adapter']=='whatsapp_call']
            servers={s['id']:s for s in config.get('servers',[])}
            if (len(calls)!=1 or servers.get(calls[0]['server'],{}).get('module')!='me3_whatsapp.call_server'
                or servers[calls[0]['server']]['read_tools']!=[] or policy.get('whatsapp_call_kinds')!=['voice']
                or policy.get('whatsapp_call_mode')!='native_browser_exclusive_voice_handoff'
                or policy.get('whatsapp_call_qualification_only') is not True
                or policy.get('whatsapp_qualification_outbound_only')!=[dict(name='CONTACT_LABEL',phone='+12025550123')]):raise ValueError('bounded_native_CONTACT_LABEL_voice_call_authority_required')
            profile=policy.get('whatsapp_call_UI_profile',{});evidence=Path(profile.get('evidence',''))
            if evidence.is_absolute() or '..' in evidence.parts or not evidence.parts or not (root/evidence).is_file() or digest(root/evidence)!=profile.get('evidence_sha256') or profile.get('direct_labels')!={'voice':'Voice call'}:raise ValueError('inspected_exact_call_control_evidence_required')
        if policy.get('routine_local_reminders') is True:
            expected.append('propose_reminder')
            reminder=[a for a in config['actions'] if a['name']=='propose_reminder' and a['adapter']=='reminder_control']
            if len(reminder)!=1 or next((s for s in config['servers'] if s['id']==reminder[0]['server']),{}).get('module')!='me3_assistant.reminder_server':raise ValueError('local_reminder_authority_required')
        if policy.get('routine_productivity') is True:
            expected.append('propose_productivity')
            productivity=[a for a in config['actions'] if a['name']=='propose_productivity' and a['adapter']=='productivity_control']
            if policy.get('productivity_scope')!='pi_local_tasks_events_notes' or len(productivity)!=1 or next((s for s in config['servers'] if s['id']==productivity[0]['server']),{}).get('module')!='me3_productivity.server':raise ValueError('local_productivity_authority_required')
        if policy.get('routine_gmail_messages') is True:
            expected.append('propose_gmail_send')
            gmail=[a for a in config['actions'] if a['name']=='propose_gmail_send' and a['adapter']=='gmail_send']
            if policy.get('gmail_scope')!='commanded_reads_separately_confirmed_send' or len(gmail)!=1 or next((s for s in config['servers'] if s['id']==gmail[0]['server']),{}).get('module')!='me3_gmail.server':raise ValueError('gmail_command_and_confirmation_authority_required')
        if policy.get('routine_gmail_reads') is True:
            expected.append('propose_gmail_read')
            gmail_reads=[a for a in config['actions'] if a['name']=='propose_gmail_read' and a['adapter']=='gmail_read']
            server=next((s for s in config['servers'] if s['id']==gmail_reads[0]['server']),{}) if len(gmail_reads)==1 else {}
            if policy.get('gmail_read_scope')!='exact_review_separate_confirmation' or server.get('module')!='me3_gmail.server' or server.get('read_tools')!=[]:raise ValueError('gmail_read_consent_authority_required')
        if policy.get('routine_google_calendar') is True:
            from me3_google_calendar.budget import validate_configuration
            validate_configuration(policy,config)
            expected.append('propose_google_calendar')
        if policy.get('allowed_actions')!=expected or not any(a['name']=='propose_music_control' and a['adapter']=='music_control' for a in config.get('actions',[])):raise ValueError('operating_actions_not_authorized')
    elif policy.get('allowed_actions')!=[]:raise ValueError('operating_actions_not_authorized')
    if policy.get('hosted_requests_per_minute')!=14 or policy.get('max_input_seconds')!=120:raise ValueError('invalid_operating_resource_policy')
    selected(policy,backend);audio_mode(policy)
    for entry in config['approved_modules']:
        if source['source_hashes'].get(entry['module'].replace('.','/')+'.py')!=entry['sha256']:raise ValueError('server_module_not_in_approved_source')
    from me3_assistant.guard import legacy_running
    if legacy_running():raise ValueError('legacy_voice_session_active')
    grant={**policy,**authority,'resource_policy_path':str(resource),'resource_policy_sha256':digest(resource),'profile':'standing','mode':mode,'policy_path':str(path),'policy_sha256':digest(path),'operation_state_path':str(root/'evidence/assistant/operation-state.json')}
    if policy.get('routine_local_music') is True:
        from me3_assistant.standing_music import StandingMusicBudget
        StandingMusicBudget(OperatingBudget(grant,{},root/'evidence/completion/autonomous-20261020-001'),grant,root/'evidence/assistant/standing-music-actions.json')._used()
    if policy.get('routine_whatsapp_messages') is True or policy.get('routine_whatsapp_contact_messages') is True:
        from me3_whatsapp.budget import StandingMessageBudget
        StandingMessageBudget(OperatingBudget(grant,{},root/'evidence/completion/autonomous-20261020-001'),grant,root/'evidence/assistant/standing-message-actions.json')._used()
    if policy.get('routine_whatsapp_window') is True:
        from me3_whatsapp.window_budget import StandingWindowBudget
        StandingWindowBudget(OperatingBudget(grant,{},root/'evidence/completion/autonomous-20261020-001'),grant,root/'evidence/assistant/standing-window-actions.json')._state()
    if policy.get('routine_whatsapp_calls') is True and mode=='normal':
        from me3_whatsapp.call_budget import StandingCallBudget
        StandingCallBudget(OperatingBudget(grant,{},root/'evidence/completion/autonomous-20261020-001'),grant,root/'evidence/assistant/standing-call-actions.json')._state()
    if policy.get('routine_productivity') is True or policy.get('routine_local_reminders') is True:
        from me3_productivity.budget import StandingProductivityBudget
        StandingProductivityBudget(OperatingBudget(grant,{},root/'evidence/completion/autonomous-20261020-001'),grant,root/'evidence/assistant/standing-productivity-actions.json').state()
    if policy.get('routine_gmail_messages') is True:
        from me3_gmail.budget import StandingGmailBudget
        StandingGmailBudget(OperatingBudget(grant,{},root/'evidence/completion/autonomous-20261020-001'),grant,root/'evidence/assistant/standing-gmail-actions.json').state()
    if policy.get('routine_gmail_reads') is True:
        from me3_gmail.read_budget import StandingGmailReadBudget
        StandingGmailReadBudget(OperatingBudget(grant,{},root/'evidence/completion/autonomous-20261020-001'),grant,root/'evidence/assistant/standing-gmail-read-actions.json').state()
    if policy.get('routine_google_calendar') is True:
        from me3_google_calendar.budget import StandingCalendarBudget
        StandingCalendarBudget(OperatingBudget(grant,{},root/'evidence/completion/autonomous-20261020-001'),grant,root/'evidence/assistant/standing-google-calendar-actions.json').state()
    # None means no lifetime reservation ceiling. This is never passed to the
    # legacy budget path, whose finite historical authority is unchanged.
    return grant,{k:None if k!='commit_item' else 0 for k in ROLES}

class OperatingBudget:
    def __init__(self,grant,limits,phase,clock=time.time):
        self.grant=grant;self.phase=Path(phase);self.exhausted=asyncio.Event();self.clock=clock
        self.path=Path(grant['operation_state_path']);self.observed=None
    def _state(self):
        if digest(self.grant['policy_path'])!=self.grant['policy_sha256'] or digest(self.grant['resource_policy_path'])!=self.grant['resource_policy_sha256']:raise ValueError('operating_policy_changed')
        state=read(self.path);usage=read(self.phase/'budget.json')
        if state.get('policy_sha256')!=self.grant['policy_sha256']:raise ValueError('operating_state_authority_mismatch')
        # Complete a conservatively charged interrupted reservation before any
        # further dispatch. Never replay a provider call or refund the attempt.
        if state.get('pending'):
            pending=state['pending']
            before,after=pending['before'],pending['after']
            role=pending['role']
            if role not in {'asr','reasoning','tts'} or set(before)!=ROLES or set(after)!=ROLES or any(type(before[k]) is not int or after[k]!=before[k]+(1 if k==role else 0) for k in ROLES):raise ValueError('invalid_pending_operating_reservation')
            if state['usage']!=before or usage not in (before,after):raise ValueError('operating_counter_conflict')
            atomic(self.phase/'budget.json',after)
            state['usage']=after;state['pending']=None;atomic(self.path,state);usage=after
        if set(usage)!=ROLES or state.get('usage')!=usage or any(type(usage[k]) is not int or usage[k]<state['baseline'][k] for k in ROLES):raise ValueError('operating_counter_decreased_or_changed')
        if self.observed and any(usage[k]<self.observed[k] for k in ROLES):raise ValueError('operating_counter_decreased')
        self.observed=dict(usage)
        return state,usage
    def _rate(self,state):
        now=self.clock()
        rate=state.get('hosted_attempts',[])
        if not isinstance(rate,list) or any(type(t) not in (int,float) or not math.isfinite(t) or t<0 for t in rate):raise ValueError('invalid_operating_rate_state')
        if any(t>now+1 for t in rate):raise ValueError('operating_clock_moved_backwards')
        return [t for t in rate if now-t<60]
    def remaining(self,role):
        from me3_assistant.guard import lock
        with lock(self.phase/'normal-reservation.lock',timeout=ACCOUNTING_LOCK_TIMEOUT):
            state,_=self._state()
            if self.grant.get('mode')=='text' and role!='reasoning':return 0
            if role in {'asr','reasoning'}:return max(0,14-len(self._rate(state)))
            return 2**63-1 if role=='tts' else 0
    def permitted(self,role):return self.remaining(role)>0
    def reserve(self,role):
        from me3_assistant.guard import lock,legacy_running
        with lock(self.phase/'normal-reservation.lock',timeout=ACCOUNTING_LOCK_TIMEOUT):
            if legacy_running():raise ValueError('legacy_voice_session_active')
            state,usage=self._state();rate=self._rate(state)
            if role not in {'asr','reasoning','tts'} or self.grant.get('mode')=='text' and role!='reasoning' or role in {'asr','reasoning'} and len(rate)>=14:
                self.exhausted.set();raise ProviderLimited('standing_rate_or_action_policy')
            after={**usage,role:usage[role]+1}
            if role in {'asr','reasoning'}:rate.append(self.clock())
            state['hosted_attempts']=rate
            state['pending']=dict(before=usage,after=after,role=role)
            atomic(self.path,state)
            atomic(self.phase/'budget.json',after)
            state.update(usage=after,pending=None);atomic(self.path,state)
            self.observed=dict(after)
            return after[role]
