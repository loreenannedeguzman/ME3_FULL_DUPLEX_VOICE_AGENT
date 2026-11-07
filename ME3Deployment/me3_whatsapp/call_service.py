"""Private calling bridge: exact review -> signed host closure -> once-only kernel.

No endpoint interprets conversation or supplies confirmation to the planner.
The inherited lifecycle persists dispatch before a single browser dial and
retains its claim whenever the actual call outcome is unknown.
"""
import asyncio,hashlib,hmac,json,math,os,re,secrets,sqlite3,time
from pathlib import Path
from .call_lifecycle import CallLifecycle,Refused,digest,identity
from .recipients import Command,Directory
from me3_assistant.intent import request_binding

CONFIRMATION='direct_current_user_after_completed_prompt'

def source(root):
    return json.loads((Path(root)/'evidence/assistant/candidate-source.json').read_bytes())['revision']

def policy(root):
    root=Path(root);raw=(root/'config/operating_policy.json').read_bytes();v=json.loads(raw)
    s=json.loads((root/'evidence/assistant/candidate-source.json').read_bytes());a=json.loads((root/'evidence/assistant/operating-authorization.json').read_bytes())
    if s.get('source_hashes',{}).get('config/operating_policy.json')!=hashlib.sha256(raw).hexdigest() or a.get('approved') is not True or a.get('source_revision')!=s['revision'] or a.get('resource_policy_sha256')!=hashlib.sha256(raw).hexdigest():raise Refused('current_source_bound_call_policy_required')
    if (v.get('routine_whatsapp_calls') is not True or 'propose_whatsapp_call' not in v.get('allowed_actions',[])
        or v.get('whatsapp_qualification_outbound_only')!=[dict(name='CONTACT_LABEL',phone='+12025550123')]):
        raise Refused('source_bound_CONTACT_LABEL_call_policy_required')
    return v

class CallAuthority:
    """Developer/host-only signer. There is no sign/authorize MCP endpoint."""
    def __init__(self,root,clock=time.time):
        self.root=Path(root).resolve();self.clock=clock;self.path=self.root/'data/whatsapp-call-host-key.json'
    def key(self):
        p=self.path;p.parent.mkdir(parents=True,exist_ok=True)
        if not p.exists():
            try:
                fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
            except FileExistsError:pass
            else:
                with os.fdopen(fd,'w') as f:json.dump(dict(key=secrets.token_hex(32)),f);f.flush();os.fsync(f.fileno())
        if not p.resolve().is_relative_to(self.root/'data') or p.is_symlink() or p.parent.is_symlink() or not p.is_file() or p.stat().st_nlink!=1 or p.stat().st_size>512 or os.name!='nt' and p.stat().st_mode&0o777!=0o600:raise Refused('private_host_call_key_required')
        k=json.loads(p.read_bytes()).get('key')
        if not isinstance(k,str) or len(k)!=64:raise Refused('invalid_host_call_key')
        return bytes.fromhex(k)
    def sign(self,value):
        v=dict(value);v.pop('mac',None)
        return dict(v,mac=hmac.new(self.key(),json.dumps(v,sort_keys=True,separators=(',',':'),allow_nan=False).encode(),'sha256').hexdigest())
    def verify(self,v,*,closed=False,action='commit'):
        if not isinstance(v,dict) or not hmac.compare_digest(self.sign(v)['mac'],str(v.get('mac',''))):raise Refused('signed_host_call_proof_required')
        now=self.clock()
        valid_sources={'host_exact_review_confirmation'} if action=='commit' else {'host_exact_review_confirmation','host_recovered_call_hold'}
        if (v.get('source_revision')!=source(self.root) or v.get('source') not in valid_sources
            or v.get('confirmation_source')!=CONFIRMATION or v.get('action')!=action
            or any(not isinstance(v.get(k),str) or len(v[k])!=32 for k in ('owner','gui_session','operation_id'))
            or type(v.get('expires')) not in (int,float) or not math.isfinite(v['expires'])
            or type(v.get('issued_at')) not in (int,float) or not v['issued_at']<=now<=v['expires']
            or v['expires']-v['issued_at']>120):raise Refused('current_exact_host_call_proof_required')
        if closed and v.get('voice_closed') is not True:raise Refused('actual_voice_closure_required')
        policy(self.root);return v

class ClosedVoiceAudio:
    """No audio capture here. Native Chromium owns hardware after host closure."""
    def __init__(self,proof,authority):self.proof=proof;self.authority=authority
    async def acquire(self,owner,op):
        v=self.authority.verify(self.proof,closed=True)
        if (owner,op)!=(v['owner'],v['operation_id']):raise Refused('exact_closed_voice_owner_required')
        return dict(ready=True,asr_suspended=True,assistant_output_quiet=True,owned_lease=True)
    async def mark_dispatch(self,*args):pass
    async def sync_observation(self,*args):pass
    async def abort_before_dispatch(self,*args):pass
    async def uncertain(self,*args):pass # HostCallSession keeps the GUI voice fence held.
    async def release(self,*args):pass # Host independently validates terminal state before releasing.
    async def authorize_hangup(self,owner,op,request):
        if not isinstance(request,dict) or request.get('source')!='authenticated_gui' or request.get('owner')!=owner or request.get('operation_id')!=op or request.get('action')!='end_call':raise Refused('current_authenticated_GUI_end_required')

class CallService:
    def __init__(self,root,backend,browser_factory,*,clock=time.time,local_end=None,attest=None,local_observe=None):
        self.root=Path(root).resolve();self.backend=backend;self.browser_factory=browser_factory;self.clock=clock;self.authority=CallAuthority(root,clock);self.local_end=local_end;self.attest=attest;self.local_observe=local_observe
        self.path=self.root/'data/whatsapp-call-operations.sqlite3';self.active_proof=None;self.active_generation=None
        # One inherited durable call kernel, not a second competing lifecycle.
        self.kernel=CallLifecycle(self.root,self.path,None,None,self.resolve,self.current,self.allowed,clock)
        with self.kernel.db() as c:
            c.execute('CREATE TABLE IF NOT EXISTS host_authorizations (operation TEXT PRIMARY KEY, proof TEXT, request TEXT, authorized INTEGER DEFAULT 0, terminal TEXT)')
            c.execute('CREATE TABLE IF NOT EXISTS host_authorization_history (sequence INTEGER PRIMARY KEY AUTOINCREMENT,operation TEXT,proof TEXT,reason TEXT,at REAL)')
            c.execute('CREATE TABLE IF NOT EXISTS host_end_requests (operation TEXT PRIMARY KEY,owner TEXT,gui_session TEXT,proof_sha256 TEXT,requested_at REAL,dispatched INTEGER DEFAULT 0)')
            c.execute('CREATE TABLE IF NOT EXISTS call_failures (sequence INTEGER PRIMARY KEY AUTOINCREMENT,operation TEXT,phase TEXT,exception_type TEXT,reason TEXT,at REAL)')
    def record_failure(self,op,phase,exc):
        identity(op)
        if phase not in {'connecting','attesting','kernel_commit','local_end','local_observe','outcome_observation'}:raise Refused('bounded_call_failure_phase_required')
        known={'call_state_profile_unqualified_unknown_not_ended','call_view_unknown_not_ended','call_state_unknown_not_guessed','supported_bound_inactive_call_control_required','current_visibly_verified_selection_required','reviewed_contact_changed','recipient_outside_call_qualification_scope','private_current_confirmed_call_authority_required','dial_outcome_uncertain_no_retry','exact_fresh_call_preflight_required','parent_target_changed','call_parent_target_changed','call_recipient_changed','owned_call_session_required','independent_owned_browser_session_required','one_exact_direct_call_control_no_draft_or_dialog_required','local_end_not_verified'}
        known.update({'draft_call_or_unknown_dialog_preserved','visibly_verified_current_person_required','selected_person_expired','selected_parent_target_changed','preexisting_other_WhatsApp_view_requires_inspection','finite_call_preflight_clock_required','call_before_phone_identity_required','call_before_phone_draft_preserved','call_before_phone_unknown_dialog_preserved','call_before_phone_active_call_preserved','call_before_phone_context_required'})
        reason=str(exc) if isinstance(exc,Refused) or type(exc).__name__=='Unsupported' else ''
        reason=reason if reason in known else 'unspecified'
        if reason in {'call_state_profile_unqualified_unknown_not_ended','call_view_unknown_not_ended','call_state_unknown_not_guessed'}:reason='observation_not_qualified'
        name=type(exc).__name__;name=name if re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,63}',name) else 'Exception'
        with self.kernel.db() as c:c.execute('INSERT INTO call_failures(operation,phase,exception_type,reason,at) VALUES (?,?,?,?,?)',(op,phase,name,reason,self.clock()))
    def current(self,owner,generation):
        return bool(self.active_proof and self.active_proof['owner']==owner and self.active_generation==generation)
    def allowed(self,p):policy(self.root);return p.get('label')=='CONTACT_LABEL' and p.get('phone')=='+12025550123'
    def resolve(self,owner,selection):
        v=self.active_proof
        if not v or v['owner']!=owner:raise Refused('bound_current_call_owner_required')
        command=Command(owner,v['goal'],v['generation'],v['request']);directory=Directory(self.root/'data/whatsapp-directory.sqlite3',self.clock)
        r=directory.resolve_draft(command,selection,lambda c:True)
        with directory.db() as c:
            s=c.execute('SELECT expires FROM selections WHERE id=? AND owner=? AND revoked=0',(selection,owner)).fetchone()
            chosen=c.execute('SELECT selection_id,expires FROM selected_contacts WHERE owner=?',(owner,)).fetchone()
        if not s or not chosen or chosen['selection_id']!=selection:raise Refused('explicitly_selected_verified_call_recipient_required')
        return dict(owner=owner,selection_id=selection,identity_verified=True,label=r['destination']['label'],phone=r['destination']['phone'],target_id=r['target_id'],binding=r['binding'],expires=min(s['expires'],chosen['expires']))
    async def prepare(self,op,selection,owner,goal,request,generation):
        policy(self.root);command=Command(owner,goal,generation,request);command.validate()
        self.active_proof=dict(owner=owner,goal=goal,generation=generation,request=request);self.active_generation=generation
        try:
            result=await self.kernel.prepare(op,owner,generation,selection,'voice',request_binding(owner,goal,generation,request)['request_sha256'])
            with self.kernel.db() as c:c.execute('INSERT INTO host_authorizations(operation,proof,request) VALUES (?,?,?)',(op,'{}',json.dumps(self.active_proof)))
            return dict(operation_id=op,intent=result['intent'],expected_digest=result['expected_digest'],token=result['private_token'],request_binding=request_binding(owner,goal,generation,request))
        finally:self.active_proof=None;self.active_generation=None
    def authorize(self,p,g,gui_session):
        """Synchronous trusted host import at the exact confirmed review edge."""
        if p.get('_confirmation_source')!=CONFIRMATION or p.get('_confirmed_generation')!=g:raise Refused('completed_separate_host_confirmation_required')
        op=identity(p['operation_id']);identity(gui_session);row=self.kernel.row(op);proposal=p['proposal']
        binding=p.get('_handoff_binding')
        if not isinstance(binding,dict) or Path(binding.get('root','')).resolve()!=self.root or Path(binding.get('journal_path','')).resolve()!=self.root/'data/assistant-actions.jsonl' or Path(binding.get('ledger_path','')).resolve()!=self.root/'data/onit-action-intents.jsonl' or not re.fullmatch('[0-9a-f]{32}',binding.get('goal_id','')) or not re.fullmatch('[0-9a-f]{64}',binding.get('intent_key','')):raise Refused('canonical_host_call_journal_binding_required')
        handoff=dict(goal_id=binding['goal_id'],intent_key=binding['intent_key'])
        if row['state']!='PREPARED' or row['expires']<=self.clock() or row['intent_hash']!=p['expected_digest'] or not secrets.compare_digest(row['token'],proposal['token']):raise Refused('exact_current_call_review_required')
        with self.kernel.db() as c:
            a=c.execute('SELECT * FROM host_authorizations WHERE operation=?',(op,)).fetchone()
            if not a or a['authorized'] or c.execute('SELECT 1 FROM host_authorizations WHERE authorized=1 AND terminal IS NULL').fetchone():raise Refused('call_handoff_not_replayed_or_overlapped')
            request=json.loads(a['request']);v=dict(source='host_exact_review_confirmation',source_revision=source(self.root),confirmation_source=CONFIRMATION,action='commit',owner=row['owner'],gui_session=gui_session,operation_id=op,intent_hash=row['intent_hash'],token=row['token'],generation=g,issued_at=self.clock(),expires=min(row['expires'],self.clock()+120),expires_monotonic=time.monotonic()+min(120,row['expires']-self.clock()),voice_closed=False,goal=request['goal'],handoff=handoff)
            self.authority.verify(self.authority.sign(v));c.execute('UPDATE host_authorizations SET authorized=1,proof=? WHERE operation=?',(json.dumps(v),op))
        return v
    def authorized_row(self,proof,*,action='commit'):
        self.authority.verify(proof,closed=True,action=action);op=proof['operation_id'];r=self.kernel.row(op)
        with self.kernel.db() as c:a=c.execute('SELECT * FROM host_authorizations WHERE operation=?',(op,)).fetchone()
        if not a or a['authorized']!=1 or proof['intent_hash']!=r['intent_hash'] or proof['owner']!=r['owner'] or not secrets.compare_digest(proof['token'],r['token']):raise Refused('durable_exact_call_authorization_required')
        old=json.loads(a['proof'])
        if any(proof.get(k)!=old.get(k) for k in ('gui_session','operation_id','intent_hash','owner','generation','goal','source_revision','handoff')):raise Refused('host_call_binding_changed')
        return r,a
    async def commit(self,proof):
        row,a=self.authorized_row(proof)
        if row['state']!='PREPARED':return self.public(row['id']) # Never redial an uncertain/committed operation.
        self.active_proof=json.loads(a['request']);self.active_generation=proof['generation'];self.kernel.audio=ClosedVoiceAudio(proof,self.authority)
        self.kernel.browser=self.browser_factory(row,proof)
        phase='connecting'
        try:
            try:
                async with self.backend.transaction(False):
                    phase='attesting'
                    if self.attest is not None:await self.attest(row,proof)
                    phase='kernel_commit'
                    await self.kernel.commit(row['id'],row['token'],'confirm',proof['generation'])
            except Exception as exc:
                self.record_failure(row['id'],phase,exc)
                # Unknown after dispatch stays held. Connection, attestation
                # or preflight failure can be durably proven no-dial only
                # when no dispatch event or browser session ever existed.
                state=self.kernel.row(row['id'])['state']
                if state in {'PREPARED','CLAIMED','ABORTED'}:
                    self.abort_before_dispatch(self.authority.sign(dict(proof,action='abort')))
            return self.public(row['id'])
        finally:self.active_proof=None;self.active_generation=None
    def public(self,op):
        r=self.kernel.row(op)
        with self.kernel.db() as c:a=c.execute('SELECT terminal FROM host_authorizations WHERE operation=?',(op,)).fetchone()
        if a and a['terminal']:return json.loads(a['terminal'])
        state={'PREPARED':'HELD_UNCERTAIN','CLAIMED':'HELD_UNCERTAIN','UNCERTAIN':'HELD_UNCERTAIN','ABORTED':'NO_DIAL_ABORTED'}.get(r['state'],r['state'])
        result=dict(operation_id=op,state=state,verified=r['state'] in {'DIALING','RINGING','CONNECTED','ENDED'},execution_started=r['state'] not in {'PREPARED','ABORTED','CANCELED'},remote_end_confirmed=r['state']=='ENDED',write_retried=False)
        if r['state']=='ENDED':
            # Kernel ENDED requires its own authenticated fresh browser
            # observation. Save that exact terminal fact for restart privacy.
            with self.kernel.db() as c:c.execute('UPDATE host_authorizations SET terminal=? WHERE operation=? AND terminal IS NULL',(json.dumps(result),op))
        return result
    def recovery(self):
        with self.kernel.db() as c:rows=c.execute('SELECT operation,proof FROM host_authorizations WHERE authorized=1 AND terminal IS NULL').fetchall()
        return [dict(proof=json.loads(r['proof']),result=self.public(r['operation'])) for r in rows]
    async def adopt_recovery(self,record,new_gui_session,audit):
        """Trusted GUI-only recovery. It cannot dial or clear an unknown call."""
        identity(new_gui_session)
        if not isinstance(record,dict) or 'proof' not in record:raise Refused('durable_call_hold_required')
        old=record['proof'];op=identity(old['operation_id']);row=self.kernel.row(op)
        with self.kernel.db() as c:a=c.execute('SELECT * FROM host_authorizations WHERE operation=? AND authorized=1 AND terminal IS NULL',(op,)).fetchone()
        if not a or json.loads(a['proof'])!=old:raise Refused('unchanged_durable_call_hold_required')
        proof=await audit(record,new_gui_session)
        if not isinstance(proof,dict) or proof.get('operation_id')!=op or proof.get('new_gui_session')!=new_gui_session or proof.get('old_ME3_processes_closed') is not True or proof.get('old_ME3_audio_closed') is not True or proof.get('authenticated_GUI_adoption') is not True:raise Refused('independent_old_voice_closure_and_GUI_adoption_required')
        policy(self.root)
        with self.kernel.db() as c:requested=c.execute('SELECT owner FROM host_end_requests WHERE operation=?',(op,)).fetchone()
        now=self.clock();v=dict(old,source='host_recovered_call_hold',source_revision=source(self.root),original_source_revision=old.get('original_source_revision',old['source_revision']),gui_session=new_gui_session,issued_at=now,expires=now+30,voice_closed=True,end_requested=bool(requested and requested['owner']==row['owner']))
        with self.kernel.db() as c:
            c.execute('INSERT INTO host_authorization_history(operation,proof,reason,at) VALUES (?,?,?,?)',(op,a['proof'],'audited_terminal_only_GUI_recovery',now))
            if c.execute('UPDATE host_authorizations SET proof=? WHERE operation=? AND proof=? AND terminal IS NULL',(json.dumps(v),op,a['proof'])).rowcount!=1:raise Refused('call_hold_changed_during_recovery')
        return v
    def abort_before_dispatch(self,proof):
        row,a=self.authorized_row(proof,action='abort');op=row['id']
        with self.kernel.db() as c:
            sent=c.execute("SELECT 1 FROM events WHERE id=? AND state='DISPATCHED'",(op,)).fetchone()
            if sent or row['session'] is not None or row['state'] not in {'PREPARED','CLAIMED','ABORTED'}:raise Refused('durable_no_dial_proof_required')
            c.execute("UPDATE calls SET state='ABORTED' WHERE id=?",(op,));c.execute('INSERT INTO events(id,state,at) VALUES (?,?,?)',(op,'ABORTED',self.clock()))
            result=dict(operation_id=op,state='NO_DIAL_ABORTED',verified=False,execution_started=False,remote_end_confirmed=False,write_retried=False)
            c.execute('UPDATE host_authorizations SET terminal=? WHERE operation=?',(json.dumps(result),op))
        self.kernel.free_slot(op);return result
    async def end(self,proof,request):
        row,a=self.authorized_row(proof,action='end');op=row['id']
        self.mark_end_requested(proof,request)
        if row['state'] in {'PREPARED','CLAIMED','ABORTED'} and row['session'] is None:
            return self.abort_before_dispatch(self.authority.sign(dict(proof,action='abort')))
        # Explicit end may use a qualified owned-browser close mechanism. It
        # never calls dial/commit and must distinguish local closure from an
        # acknowledged remote call ending.
        if self.local_end is None:return dict(operation_id=op,state='HELD_UNCERTAIN',verified=False,remote_end_confirmed=False)
        with self.kernel.db() as c:
            claimed=c.execute('UPDATE host_end_requests SET dispatched=1 WHERE operation=? AND dispatched=0',(op,)).rowcount
        if not claimed:
            # A lost End reply is reconciled by observation, never by another
            # close effect. Even a second signed End cannot repeat it.
            return await self.reconcile(self.authority.sign(dict(proof,action='reconcile')),dict(request,action='check_call_status'))
        try:result=await self.local_end(row,request)
        except Exception as exc:self.record_failure(op,'local_end',exc);raise
        return self.record_local_terminal(row,result)
    def mark_end_requested(self,proof,request):
        """Host-only durable command edge, before the GUI's End RPC starts."""
        row,a=self.authorized_row(proof,action='end');op=row['id']
        if not isinstance(request,dict) or request.get('source')!='authenticated_gui' or request.get('operation_id')!=op or request.get('owner')!=row['owner'] or request.get('gui_session')!=proof['gui_session'] or request.get('action')!='end_call':raise Refused('exact_GUI_end_command_required')
        with self.kernel.db() as c:
            previous=c.execute('SELECT owner FROM host_end_requests WHERE operation=?',(op,)).fetchone()
            if previous and previous['owner']!=row['owner']:raise Refused('End_command_owner_changed')
            c.execute('INSERT OR IGNORE INTO host_end_requests(operation,owner,gui_session,proof_sha256,requested_at) VALUES (?,?,?,?,?)',(op,row['owner'],proof['gui_session'],digest(proof),self.clock()))
    async def reconcile(self,proof,request):
        """Existing owned operation only. NO dial, close, UI input or retry."""
        row,a=self.authorized_row(proof,action='reconcile');op=row['id']
        if not isinstance(request,dict) or request.get('source')!='authenticated_gui' or request.get('operation_id')!=op or request.get('owner')!=row['owner'] or request.get('gui_session')!=proof['gui_session'] or request.get('action')!='check_call_status':raise Refused('exact_GUI_call_status_command_required')
        with self.kernel.db() as c:ended=c.execute('SELECT * FROM host_end_requests WHERE operation=?',(op,)).fetchone()
        if not ended or ended['owner']!=row['owner'] or not (ended['gui_session']==proof['gui_session'] or proof.get('source')=='host_recovered_call_hold' and proof.get('end_requested') is True):raise Refused('prior_owner_End_command_required')
        result=self.public(op)
        if result['state'] in {'LOCAL_ENDED','NO_DIAL_ABORTED','ENDED'}:return result
        if row['state'] in {'PREPARED','CLAIMED','ABORTED'} and row['session'] is None:return self.abort_before_dispatch(self.authority.sign(dict(proof,action='abort')))
        if self.local_observe is None:return dict(operation_id=op,state='HELD_UNCERTAIN',verified=False,remote_end_confirmed=False)
        try:observed=await self.local_observe(row)
        except Exception as exc:self.record_failure(op,'local_observe',exc);raise
        return self.record_local_terminal(row,observed)
    def record_local_terminal(self,row,result):
        op=row['id']
        if not isinstance(result,dict) or result.get('operation_id')!=op or result.get('owned_browser_closed') is not True or result.get('owned_streams_closed') is not True or result.get('independent') is not True:
            self.record_failure(op,'outcome_observation',Refused('local_end_not_verified'))
            return dict(operation_id=op,state='HELD_UNCERTAIN',verified=False,remote_end_confirmed=False)
        terminal=dict(operation_id=op,state='LOCAL_ENDED',verified=False,owned_browser_closed=True,owned_streams_closed=True,independent=True,remote_end_confirmed=False,write_retried=False)
        with self.kernel.db() as c:
            c.execute('UPDATE host_authorizations SET terminal=? WHERE operation=?',(json.dumps(terminal),op))
            c.execute("UPDATE calls SET state='LOCAL_ENDED' WHERE id=?",(op,));c.execute('INSERT INTO events(id,state,at) VALUES (?,?,?)',(op,'LOCAL_ENDED',self.clock()))
        self.kernel.free_slot(op);return terminal
