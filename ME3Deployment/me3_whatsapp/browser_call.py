"""Dormant visible-CDP call adapter. No internal WhatsApp APIs or default labels.

Default is inspection-only. A host-qualified direct-call UI profile, exact
current call authority and separately qualified audio are required before use.
The adapter never starts a browser, searches contacts, grants media permission,
chooses arbitrary tabs or interprets a disappeared window as an ended call.
"""
import asyncio, hashlib, hmac, json, math, os, re, secrets, sqlite3, time
from contextlib import closing,contextmanager
from dataclasses import dataclass,field
from pathlib import Path
from urllib.parse import urlsplit
from me3_whatsapp.contact_browser import ContactBrowser,VISIBLE
from me3_whatsapp.contact_message_browser import ContactMessageBrowser,CHECK,fill

ORIGIN='https://web.whatsapp.com'
class Unsupported(RuntimeError):pass
def packed(v):return json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
def digest(v):return hashlib.sha256(packed(v).encode()).hexdigest()

@dataclass(frozen=True)
class CallUIProfile:
    # These values must come from independently inspected UI evidence; callers
    # cannot fill them from model output. None are real WhatsApp defaults.
    evidence_sha256:str
    direct_labels:dict=field(default_factory=dict)
    root_selector:str=''
    name_selector:str=''
    phone_selector:str=''
    kind_selector:str=''
    kind_texts:dict=field(default_factory=dict)
    state_selector:str=''
    state_texts:dict=field(default_factory=dict)
    hangup_label:str=''
    def dial_supported(self,kind):
        return (kind=='voice' and re.fullmatch('[a-f0-9]{64}',self.evidence_sha256 or '') is not None
                and isinstance(self.direct_labels.get(kind),str) and 0<len(self.direct_labels[kind])<=200)
    def supported(self,kind):
        values=[self.root_selector,self.name_selector,self.phone_selector,self.kind_selector,self.state_selector,self.hangup_label]
        return (self.dial_supported(kind)
                and isinstance(self.direct_labels.get(kind),str) and bool(self.direct_labels[kind])
                and all(isinstance(v,str) and 0<len(v)<=200 for v in values)
                and isinstance(self.kind_texts.get(kind),str) and bool(self.kind_texts[kind])
                and set(self.state_texts)=={'dialing','ringing','connected','ended'}
                and all(isinstance(v,str) and v for v in self.state_texts.values())
                and len(set(self.state_texts.values()))==4)

class BrowserCall:
    def __init__(self,root,path,backend,owner,operation,*,profile=None,calls_enabled=False,
                 authority=lambda *args:False,connect_target=None,clock=time.time,monotonic=time.monotonic,allow_unqualified_observation=False):
        self.root=Path(root).resolve();self.path=Path(path)
        if self.path.is_symlink() or not self.path.resolve().is_relative_to(self.root):raise Unsupported('private_call_browser_store_required')
        if any(not isinstance(x,str) or not re.fullmatch('[a-f0-9]{32}',x) for x in (owner,operation)):raise Unsupported('host_call_identity_required')
        self.path.parent.mkdir(parents=True,exist_ok=True);fd=os.open(self.path,os.O_CREAT|os.O_RDWR|getattr(os,'O_NOFOLLOW',0),0o600);os.close(fd)
        self.backend=backend;self.owner=owner;self.operation=operation;self.profile=profile;self.enabled=calls_enabled is True
        self.authority=authority;self.connect_target=connect_target;self.clock=clock;self.monotonic=monotonic;self.preflight=None;self.view=None
        self.allow_unqualified_observation=allow_unqualified_observation is True
        with self.db() as c:
            c.execute('CREATE TABLE IF NOT EXISTS secret (id INTEGER PRIMARY KEY CHECK(id=1), key TEXT)')
            c.execute('INSERT OR IGNORE INTO secret VALUES (1,?)',(secrets.token_hex(32),))
            c.execute('CREATE TABLE IF NOT EXISTS calls (operation TEXT PRIMARY KEY, owner TEXT, person_hash TEXT, kind TEXT, parent TEXT, context TEXT, baseline TEXT, session TEXT UNIQUE, state TEXT, hangup INTEGER DEFAULT 0)')
            c.execute('CREATE TABLE IF NOT EXISTS receipts (session TEXT, sequence INTEGER, body TEXT, PRIMARY KEY(session,sequence))')
    @contextmanager
    def db(self):
        with closing(sqlite3.connect(self.path,timeout=3)) as c:
            c.row_factory=sqlite3.Row
            try:c.execute('PRAGMA synchronous=FULL');c.execute('BEGIN IMMEDIATE');yield c;c.commit()
            except BaseException:c.rollback();raise
    def check(self):
        self.backend.check()
        if self.backend.page is None:raise Unsupported('owned_existing_CDP_connection_required')
    def recipient(self,p):
        if p.get('owner')!=self.owner or p.get('identity_verified') is not True or not re.fullmatch(r'\+[1-9][0-9]{6,14}',p.get('phone','')):
            raise Unsupported('visibly_verified_current_person_required')
        if type(p.get('expires')) not in (int,float) or not math.isfinite(p['expires']) or p['expires']<=self.clock():raise Unsupported('selected_person_expired')
        return dict(target_id=p['target_id'],binding=p['binding'],destination=dict(label=p['label'],phone=p['phone']))
    def contact(self,p):return ContactMessageBrowser(self.backend,self.recipient(p),self.owner,'')
    async def safe_before_phone(self,contact):
        # Selection intentionally leaves the verified Contact info pane open.
        # Only that exact matching pane may occlude the still-empty chat here;
        # no unknown modal/call or underlying manual draft is excused by it.
        value=await contact.evaluate('(()=>{'+CHECK+VISIBLE+r'''if(!valid)return {blocked:'identity'};const rect=e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0};if(Array.from(m.querySelectorAll('footer [contenteditable="true"]')).some(e=>e.innerText.trim()!==''))return {blocked:'draft'};if(Array.from(document.querySelectorAll('[role="dialog"],[aria-modal="true"]')).some(rect))return {blocked:'dialog'};if(Array.from(document.querySelectorAll('[aria-label="End call"],[aria-label="Hang up"],[aria-label="Return to call"],[aria-label="Mute microphone"]')).some(rect))return {blocked:'call'};const headings=Array.from(document.querySelectorAll('h1,h2,h3,span,div')).filter(e=>e.children.length===0&&rect(e)&&e.textContent.trim()==='Contact info');if(headings.length!==1)return {exact_info:false};for(let p=headings[0].parentElement;p&&p!==document.body;p=p.parentElement){if(p.contains(m)||p.contains(document.querySelector('#pane-side')))break;const close=Array.from(p.querySelectorAll('[aria-label="Close"]')).filter(visible);if(close.length!==1)continue;const text=p.innerText,phones=[...new Set((text.match(/\+[1-9][0-9 ()\-]{6,24}[0-9]/g)||[]).map(s=>'+'+s.replace(/\D/g,'')))];const labels=new Set(Array.from(p.querySelectorAll('span,div')).filter(e=>e.children.length===0&&e.textContent.trim()===LABEL).map(e=>e.textContent.trim()));if(phones.length===1&&phones[0]===PHONE_NUMBER&&labels.size===1&&visible(headings[0])&&!p.querySelector('[data-icon="default-group"],[data-icon="community"]'))return {exact_info:true}}return {exact_info:false}})()''')
        if value.get('blocked'):
            reasons={'identity':'call_before_phone_identity_required','draft':'call_before_phone_draft_preserved','dialog':'call_before_phone_unknown_dialog_preserved','call':'call_before_phone_active_call_preserved'}
            raise Unsupported(reasons.get(value['blocked'],'call_before_phone_context_required'))
        if value.get('exact_info') is not True:await self.safe_parent(contact)
    async def safe_parent(self,contact):
        value=await contact.evaluate('(()=>{'+CHECK+VISIBLE+r'''if(!valid)return false;const a=Array.from(m.querySelectorAll('footer [contenteditable="true"]')).filter(visible);return a.length===1&&a[0].innerText.trim()===''&&!Array.from(document.querySelectorAll('[role="dialog"],[aria-modal="true"]')).some(visible)&&!Array.from(document.querySelectorAll('[aria-label="End call"],[aria-label="Hang up"],[aria-label="Return to call"],[aria-label="Mute microphone"]')).some(visible)})()''')
        if value is not True:raise Unsupported('draft_call_or_unknown_dialog_preserved')
    async def target(self,page=None):
        self.check();v=await (page or self.backend.page).call('Target.getTargetInfo');v=v.get('targetInfo',{})
        if not re.fullmatch('[A-Za-z0-9_-]{1,128}',v.get('targetId','')):raise Unsupported('exact_owned_target_required')
        return v
    async def targets(self):
        self.check();values=(await self.backend.page.call('Target.getTargets')).get('targetInfos',[])
        if not isinstance(values,list) or len(values)>32:raise Unsupported('bounded_owned_targets_required')
        return values
    async def inspect(self,p):
        """Read-only selected-header metadata; never profile/dial/navigation clicks."""
        contact=self.contact(p);await contact.verified()
        return await contact.evaluate('(()=>{'+CHECK+VISIBLE+r'''if(!valid)return {bound:false};const controls=Array.from(h.querySelectorAll('button,[role="button"]')).filter(visible);return {bound:true,official:true,linked:true,controls:controls.filter(e=>/call/i.test((e.getAttribute('aria-label')||'')+' '+Array.from(e.querySelectorAll('[data-icon]')).map(n=>n.getAttribute('data-icon')).join(' '))).slice(0,8).map(e=>({label:(e.getAttribute('aria-label')||'').slice(0,120),icons:Array.from(e.querySelectorAll('[data-icon]')).slice(0,4).map(n=>n.getAttribute('data-icon')),enabled:!e.disabled&&e.getAttribute('aria-disabled')!=='true'})),call_state:'unknown',fresh_phone:false,inspection_only:true}})()''')
    async def inventory(self):
        """No contact read/query/click: selected-header call-control metadata only."""
        self.check()
        return await self.backend.evaluate(r'''(()=>{if(location.origin!=='https://web.whatsapp.com'||!document.querySelector('#pane-side'))return {official:false};'''+VISIBLE+r'''const h=document.querySelector('#main header');if(!h)return {official:true,linked:true,header:false};const a=Array.from(h.querySelectorAll('button,[role="button"]')).filter(visible).filter(e=>/call/i.test((e.getAttribute('aria-label')||'')+' '+Array.from(e.querySelectorAll('[data-icon]')).map(n=>n.getAttribute('data-icon')).join(' ')));return {official:true,linked:true,header:true,controls:a.slice(0,8).map(e=>({label:(e.getAttribute('aria-label')||'').slice(0,120),icons:Array.from(e.querySelectorAll('[data-icon]')).slice(0,4).map(n=>n.getAttribute('data-icon')),enabled:!e.disabled&&e.getAttribute('aria-disabled')!=='true'})),dialogs:Array.from(document.querySelectorAll('[role="dialog"],[aria-modal="true"]')).filter(visible).length,call_state:'unknown',account_text_read:false,inspection_only:true}})()''')
    async def snapshot(self,p,kind):
        self.check();contact=self.contact(p);await contact.verified()
        if (not self.enabled or not isinstance(self.profile,CallUIProfile) or not self.profile.dial_supported(kind)
            or not self.profile.supported(kind) and not self.allow_unqualified_observation):
            return dict(bound=True,target_id=p['target_id'],active_call=None,call_available=False,unknown_dialog=None,reason='call_UI_and_audio_not_qualified')
        if self.authority(self.owner,self.operation,kind,digest(p)) is not True:raise Unsupported('private_current_confirmed_call_authority_required')
        await self.safe_before_phone(contact)
        await contact.fresh_phone()
        await self.safe_parent(contact)
        parent=await self.target()
        if parent['targetId']!=p['target_id']:raise Unsupported('selected_parent_target_changed')
        nonce=secrets.token_hex(16);label=self.profile.direct_labels[kind];selector=self.profile.root_selector or '[data-me3-unqualified-call-root]'
        source='(()=>{'+CHECK+VISIBLE+r'''if(!valid)return {bound:false};const editors=Array.from(m.querySelectorAll('footer [contenteditable="true"]')).filter(visible);const dialogs=Array.from(document.querySelectorAll('[role="dialog"],[aria-modal="true"]')).filter(visible);const active=Array.from(document.querySelectorAll(ROOT_SELECTOR)).filter(visible);if(editors.length!==1||editors[0].innerText.trim()!==''||dialogs.length||active.length)return {bound:true,draft:true};const a=Array.from(h.querySelectorAll('button,[role="button"]')).filter(e=>visible(e)&&e.getAttribute('aria-label')===CALL_LABEL&&!e.disabled&&e.getAttribute('aria-disabled')!=='true');if(a.length!==1)return {bound:true,controls:a.length};window.__me3CallPreflight={nonce:NONCE,header:h,control:a[0],editor:editors[0],kind:KIND,pinnedAt:performance.now()};return {bound:true,ready:true}})()'''
        source=source.replace('ROOT_SELECTOR',json.dumps(selector)).replace('CALL_LABEL',json.dumps(label)).replace('NONCE',json.dumps(nonce)).replace('KIND',json.dumps(kind))
        pinned_at=self.monotonic()
        if type(pinned_at) not in (int,float) or not math.isfinite(pinned_at):raise Unsupported('finite_call_preflight_clock_required')
        v=await contact.evaluate(source)
        if v.get('ready') is not True:raise Unsupported('one_exact_direct_call_control_no_draft_or_dialog_required')
        baseline=await self.targets()
        if any(x.get('type')=='page' and x.get('targetId')!=parent['targetId'] and urlsplit(x.get('url','')).netloc=='web.whatsapp.com' for x in baseline):
            raise Unsupported('preexisting_other_WhatsApp_view_requires_inspection')
        self.preflight=dict(nonce=nonce,person=digest(p),kind=kind,parent=parent,baseline=[x['targetId'] for x in baseline],pinned_at=pinned_at)
        return dict(bound=True,target_id=parent['targetId'],active_call=False,call_available=True,unknown_dialog=False,nonce=nonce,operation_id=self.operation)
    def record(self,session):
        with self.db() as c:r=c.execute('SELECT * FROM calls WHERE session=? AND operation=?',(session,self.operation)).fetchone()
        if r is None:raise Unsupported('owned_call_session_required')
        return dict(r)
    async def dial_once(self,p,kind,before):
        self.check();f=self.preflight
        with self.db() as c:
            if c.execute('SELECT 1 FROM calls WHERE operation=?',(self.operation,)).fetchone():raise Unsupported('dial_not_replayed')
        if not f or f['person']!=digest(p) or f['kind']!=kind or before.get('nonce')!=f['nonce'] or before.get('operation_id')!=self.operation:
            raise Unsupported('exact_fresh_call_preflight_required')
        age=self.monotonic()-f['pinned_at']
        if not math.isfinite(age) or not 0<=age<=3:raise Unsupported('call_preflight_expired_no_dispatch')
        if not self.enabled or self.authority(self.owner,self.operation,kind,digest(p)) is not True:raise Unsupported('call_not_enabled_or_authorized')
        # snapshot already read the actual phone before pinning exact DOM nodes.
        # Reopening its panel here can rerender those nodes. No DOM mutation is
        # allowed between that fresh phone/pin and the final once-only click.
        contact=self.contact(p);await contact.verified();await self.safe_parent(contact)
        if (await self.target())['targetId']!=f['parent']['targetId']:raise Unsupported('parent_target_changed')
        # No asynchronous gap between final DOM/control checks and the one click.
        session=secrets.token_hex(16)
        with self.db() as c:
            if c.execute('SELECT 1 FROM calls WHERE operation=?',(self.operation,)).fetchone():raise Unsupported('dial_not_replayed')
            c.execute('INSERT INTO calls(operation,owner,person_hash,kind,parent,context,baseline,session,state) VALUES (?,?,?,?,?,?,?,?,?)',
                      (self.operation,self.owner,digest(p),kind,f['parent']['targetId'],f['parent'].get('browserContextId',''),packed(f['baseline']),session,'DISPATCHED'))
        source='(()=>{'+CHECK+VISIBLE+r'''const d=window.__me3CallPreflight;if(!valid||!d||d.nonce!==NONCE||d.header!==h||!Number.isFinite(d.pinnedAt)||performance.now()-d.pinnedAt<0||performance.now()-d.pinnedAt>3000||!d.control.isConnected||!d.editor.isConnected||d.editor.innerText.trim()!==''||d.control.getAttribute('aria-label')!==CALL_LABEL||d.control.disabled||d.control.getAttribute('aria-disabled')==='true'||!visible(d.control)||Array.from(document.querySelectorAll('[role="dialog"],[aria-modal="true"],'+ROOT_SELECTOR)).some(visible))return false;const editors=Array.from(m.querySelectorAll('footer [contenteditable="true"]')).filter(visible);const controls=Array.from(h.querySelectorAll('button,[role="button"]')).filter(e=>visible(e)&&e.getAttribute('aria-label')===CALL_LABEL&&!e.disabled&&e.getAttribute('aria-disabled')!=='true');if(editors.length!==1||editors[0]!==d.editor||controls.length!==1||controls[0]!==d.control)return false;delete window.__me3CallPreflight;d.control.click();return true})()'''
        source=source.replace('NONCE',json.dumps(f['nonce'])).replace('CALL_LABEL',json.dumps(self.profile.direct_labels[kind])).replace('ROOT_SELECTOR',json.dumps(self.profile.root_selector or '[data-me3-unqualified-call-root]'))
        # Only this separately confirmed, once-reserved, atomically guarded
        # dial receives user activation. Generic reads/evaluations never do.
        # This allows a native call UI to open its owned popup/audio flow;
        # successful script completion is still not a verified call outcome.
        self.check()
        result=await self.backend.page.call('Runtime.evaluate',dict(expression=contact.expression(source),awaitPromise=True,returnByValue=True,userGesture=True))
        if result.get('exceptionDetails'):
            name=result['exceptionDetails'].get('exception',{}).get('className')
            raise Unsupported('dial_script_failed_'+(name if name in {'TypeError','ReferenceError','SyntaxError','Error'} else 'ScriptError'))
        if result.get('result',{}).get('value') is not True:raise Unsupported('dial_outcome_uncertain_no_retry')
        return session
    @staticmethod
    def popup_owned(value,row):
        return (value.get('type')=='page' and value.get('openerId')==row['parent']
                and value.get('targetId') not in json.loads(row['baseline'])
                and value.get('browserContextId','')==row['context']
                and urlsplit(value.get('url','')).scheme=='https'
                and urlsplit(value.get('url','')).netloc=='web.whatsapp.com')
    async def page_for_call(self,row):
        if self.view is not None:
            target=await self.target(self.view['page'])
            if target['targetId']!=self.view['target']:raise Unsupported('call_view_target_changed')
            return self.view['page'],self.view['target']
        root=await self.target()
        if root['targetId']!=row['parent']:raise Unsupported('call_parent_target_changed')
        present=await self.backend.evaluate('Array.from(document.querySelectorAll('+json.dumps(self.profile.root_selector)+')).some(e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0})')
        if present is True:return self.backend.page,row['parent']
        eligible=[x for x in await self.targets() if self.popup_owned(x,row)]
        if len(eligible)!=1 or self.connect_target is None:raise Unsupported('call_view_unknown_not_ended')
        page=await self.connect_target(eligible[0]);actual=await self.target(page)
        if not self.popup_owned(actual,row) or actual['targetId']!=eligible[0]['targetId']:raise Unsupported('popup_opener_or_target_changed')
        return page,actual['targetId']
    async def inventory_call_views(self,session):
        """Bounded read-only metadata for profile qualification, never state proof.

        Parent scope excludes chat/message lists. Popup body controls are read
        only after an exact new opener/context/origin ownership check.
        """
        self.check();row=self.record(session);targets=await self.targets()
        root=await self.target()
        if root['targetId']!=row['parent']:raise Unsupported('call_parent_target_changed')
        views=[(self.backend.page,row['parent'],False)]
        eligible=[x for x in targets if self.popup_owned(x,row)]
        if len(eligible)>1:raise Unsupported('ambiguous_owned_call_popups')
        if eligible and self.connect_target is not None:
            page=await self.connect_target(eligible[0]);actual=await self.target(page)
            if actual['targetId']!=eligible[0]['targetId'] or not self.popup_owned(actual,row):raise Unsupported('popup_opener_or_target_changed')
            views.append((page,actual['targetId'],True))
        result=[]
        for page,target,popup in views:
            source=r'''(()=>{if(location.origin!==ORIGIN)return {};'''+VISIBLE+r'''const popup=POPUP,roots=popup?[document.body]:[...document.querySelectorAll('#main header,[role="dialog"],[aria-modal="true"]')].filter(visible),items=new Set();for(const root of roots)for(const e of root.querySelectorAll('button,[role="button"]'))if(visible(e))items.add(e);return {controls:Array.from(items).filter(e=>/call|microphone|camera|mute|answer|decline|hang up|close/i.test((e.getAttribute('aria-label')||'')+' '+Array.from(e.querySelectorAll('[data-icon]')).map(n=>n.getAttribute('data-icon')).join(' '))).slice(0,16).map(e=>({label:(e.getAttribute('aria-label')||'').slice(0,120),icons:Array.from(e.querySelectorAll('[data-icon]')).slice(0,4).map(n=>n.getAttribute('data-icon')),enabled:!e.disabled&&e.getAttribute('aria-disabled')!=='true'})),call_state:'unknown',metadata_only:true,text_read:false}})()'''
            data=await page.evaluate(source.replace('ORIGIN',json.dumps(ORIGIN)).replace('POPUP',json.dumps(popup)))
            result.append(dict(target_id=target,popup=popup,metadata=data))
        return dict(views=result,unconnected_owned_popups=len(eligible) if self.connect_target is None else 0,state='unknown',dialed_again=False)
    async def observe(self,session,p):
        self.check();row=self.record(session)
        if not isinstance(self.profile,CallUIProfile) or not self.profile.supported(row['kind']):
            raise Unsupported('call_state_profile_unqualified_unknown_not_ended')
        if row['person_hash']!=digest(p) or row['owner']!=self.owner:raise Unsupported('call_recipient_changed')
        await self.contact(p).verified();page,target=await self.page_for_call(row)
        nonce=self.view['nonce'] if self.view is not None else secrets.token_hex(16)
        q=self.profile
        values=dict(root=q.root_selector,name=q.name_selector,phone=q.phone_selector,kind=q.kind_selector,
                    kind_text=q.kind_texts[row['kind']],state=q.state_selector,states=q.state_texts,
                    label=p['label'],number=p['phone'],session=session,nonce=nonce)
        source=r'''(()=>{if(location.origin!==ORIGIN)return {};'''+VISIBLE+r'''const x=VALUES,roots=Array.from(document.querySelectorAll(x.root)).filter(visible);if(roots.length!==1)return {};const root=roots[0];if(root.querySelector('[data-icon="default-group"],[data-icon="community"]'))return {};const exact=(s,t)=>{const a=Array.from(root.querySelectorAll(s)).filter(visible);return a.length===1&&a[0].textContent.trim()===t};if(!exact(x.name,x.label)||!exact(x.phone,x.number)||!exact(x.kind,x.kind_text))return {};const a=Array.from(root.querySelectorAll(x.state)).filter(visible);if(a.length!==1)return {};const states=Object.entries(x.states).filter(([k,v])=>v===a[0].textContent.trim());if(states.length!==1)return {};const prior=window.__me3CallView;if(prior&&(prior.root!==root||prior.session!==x.session||prior.nonce!==x.nonce))return {};window.__me3CallView={root,session:x.session,nonce:x.nonce};return {bound:true,state:states[0][0]}})()'''
        value=await page.evaluate(source.replace('ORIGIN',json.dumps(ORIGIN)).replace('VALUES',packed(values)))
        if value.get('bound') is not True:raise Unsupported('call_state_unknown_not_guessed')
        self.view=dict(page=page,target=target,nonce=nonce)
        now=self.clock()
        if type(now) not in (int,float) or not math.isfinite(now):raise Unsupported('observation_clock_invalid')
        with self.db() as c:
            seq=(c.execute('SELECT MAX(sequence) FROM receipts WHERE session=?',(session,)).fetchone()[0] or 0)+1
            body=dict(session_id=session,recipient_phone=p['phone'],state=value['state'],fresh=True,sequence=seq,observed_at=now,target_id=target)
            key=c.execute('SELECT key FROM secret WHERE id=1').fetchone()[0]
            body['proof']=hmac.new(bytes.fromhex(key),packed(body).encode(),'sha256').hexdigest()
            c.execute('INSERT INTO receipts VALUES (?,?,?)',(session,seq,packed(body)))
        return body
    def verify_observation(self,value,p):
        if not isinstance(value,dict) or type(value.get('observed_at')) not in (int,float) or not math.isfinite(value['observed_at']):return False
        if not 0<=self.clock()-value['observed_at']<=3 or value.get('recipient_phone')!=p.get('phone') or value.get('fresh') is not True:return False
        try:row=self.record(value.get('session_id'))
        except Unsupported:return False
        if row['person_hash']!=digest(p):return False
        with self.db() as c:
            actual=c.execute('SELECT body FROM receipts WHERE session=? AND sequence=?',(value.get('session_id'),value.get('sequence'))).fetchone()
            key=c.execute('SELECT key FROM secret WHERE id=1').fetchone()[0]
        raw={k:v for k,v in value.items() if k!='proof'}
        return bool(actual and actual['body']==packed(value) and isinstance(value.get('proof'),str)
                    and hmac.compare_digest(value['proof'],hmac.new(bytes.fromhex(key),packed(raw).encode(),'sha256').hexdigest()))
    async def hangup_once(self,session,p):
        row=self.record(session);value=await self.observe(session,p)
        if value['state']=='ended':return False
        with self.db() as c:
            if c.execute('UPDATE calls SET hangup=1 WHERE operation=? AND hangup=0',(self.operation,)).rowcount!=1:raise Unsupported('hangup_not_replayed')
        v=self.view
        source=r'''(()=>{if(location.origin!==ORIGIN)return false;'''+VISIBLE+r'''const d=window.__me3CallView;if(!d||d.session!==SESSION||d.nonce!==NONCE||!d.root.isConnected||!visible(d.root))return false;const a=Array.from(d.root.querySelectorAll('button,[role="button"]')).filter(e=>e.getAttribute('aria-label')===LABEL&&visible(e)&&!e.disabled&&e.getAttribute('aria-disabled')!=='true');if(a.length!==1)return false;a[0].click();return true})()'''
        replacements=dict(ORIGIN=ORIGIN,SESSION=session,NONCE=v['nonce'],LABEL=self.profile.hangup_label)
        source=re.sub(r'\b(?:ORIGIN|SESSION|NONCE|LABEL)\b',lambda x:json.dumps(replacements[x[0]]),source)
        if await v['page'].evaluate(source) is not True:raise Unsupported('hangup_outcome_uncertain_no_retry')
        return True
