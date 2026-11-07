"""Confirmed opaque app frame; only host-owned service RPC, never a URL proxy."""
import base64,hashlib,json,re,time,uuid
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlsplit
from onit_candidate.artifact_offer import need
from onit_candidate.confirmation import confirmation_intent
from task_mcp.reasoner import prompt_delivered

NAME='offer_runtime_preview'
HEX=re.compile('[0-9a-f]{64}')
def declaration():
    return dict(name=NAME,description='After starting or locating an actually healthy owned app service, offer to open it inside the existing Artifacts frame. Supply its exact service ID and immutable workspace snapshot from the verified service result. The host checks live identity and asks separate confirmation. No URL, port, OS browser, automatic opening or claim of complete functionality. Supports self-contained HTML, relative JS/CSS and fetch GET/POST; no WebSockets or navigation.',parameters=dict(type='object',properties={'service_id':dict(type='string',pattern='^[0-9a-f]{32}$'),'workspace_sha256':dict(type='string',pattern='^[0-9a-f]{64}$')},required=['service_id','workspace_sha256'],additionalProperties=False))

def guarded_runtime(root,registry):
    from onit_candidate.artifact_offer import guarded_delivery
    from me3_assistant.workspace_runtime import Runtime
    delivery,source=guarded_delivery(root,registry)
    return Runtime(delivery.workspace,root/'data/agent-runtime',root/'data/approved-wheelhouse'),source

def request_path(value):
    need(isinstance(value,str) and 1<=len(value)<=1024,'runtime_view_path_bound')
    p=urlsplit(value)
    need(not p.scheme and not p.netloc and not p.fragment and value.startswith('/') and not value.startswith('//') and '\\' not in value and all(ord(c)>=33 and ord(c)<127 for c in value),'runtime_view_relative_path_required')
    from urllib.parse import unquote
    decoded=unquote(p.path)
    need(not any(v in {'.','..'} for v in decoded.split('/')) and '\\' not in decoded and not decoded.startswith('//'),'runtime_view_path_traversal')
    return value

class Assets(HTMLParser):
    def __init__(self):super().__init__();self.paths=[]
    def handle_starttag(self,tag,attrs):
        a=dict(attrs);v=a.get('src') if tag=='script' else a.get('href') if tag=='link' and a.get('rel')=='stylesheet' else None
        if v is not None:
            need(isinstance(v,str) and not urlsplit(v).scheme and not urlsplit(v).netloc and not v.startswith(('/', '\\')) and '?' not in v and '#' not in v,'runtime_view_local_asset_required')
            need(v.endswith('.js' if tag=='script' else '.css'),'runtime_view_asset_type')
            request_path('/'+v);self.paths.append(v)

class RuntimePreview:
    def __init__(self,context,*,clock=time.monotonic):
        self.context=context;self.clock=clock;self.pending=None;self.event=None;self.active=None;self.opening=None;self.inactive=None
    def identity(self):return self.context.conversation_id,self.context.revision
    def clear(self,*,close=False):
        self.pending=None;self.event=None;self.opening=None
        if close:self.active=None;self.inactive=None
    def inactive_identity(self,source=None):
        """Bounded nonauthorizing identity, not a browser acknowledgement."""
        p=self.inactive
        if not p:return None
        if (self.active is not None or self.context.artifact_preview.active is not None
            or p['context']!=self.identity() or not p['closed_at']<=self.clock()<p['expires']
            or source is not None and p['identity']['source_revision']!=source):self.inactive=None;return None
        return dict(p['identity'])
    def revoke_open(self):self.event=None;self.opening=None
    def valid_pending(self,p):return bool(p and p['context']==self.identity() and self.clock()<p['expires'])
    def valid_active(self,p,source):return bool(p and p['conversation_id']==self.context.conversation_id and p['source']==source and self.clock()<p['expires'])
    async def live(self,runtime,args):
        row=await runtime.status(args['service_id'])
        need(row.get('status')=='running' and row.get('outcome_verified') is True and row.get('owned_namespace_closed') is False and row.get('service_id')==args['service_id'] and row.get('workspace_manifest_sha256')==args['workspace_sha256'],'runtime_view_live_snapshot_required')
        return row
    @staticmethod
    def birth(row):
        need(type(row.get('pid_namespace_inode')) is int and isinstance(row.get('init_birth'),dict),'runtime_view_owned_birth_required')
        return dict(pid_namespace_inode=row['pid_namespace_inode'],init_birth=row['init_birth'])
    async def fetch(self,runtime,args,**request):
        row=await runtime.view(args['service_id'],args['workspace_sha256'],**request)
        need(row.get('ok') is True and row.get('status')=='view' and row.get('service_id')==args['service_id'] and row.get('workspace_manifest_sha256')==args['workspace_sha256'] and row.get('outcome_verified') is True and row.get('owned_namespace_closed') is False and row.get('namespace_local_only') is True and row.get('host_listening_port') is False,'runtime_view_proof_required')
        raw=base64.b64decode(row['body_base64'],validate=True)
        need(len(raw)<=65536 and row['body_bytes']==len(raw) and row['body_sha256']==hashlib.sha256(raw).hexdigest(),'runtime_view_body_proof')
        need(type(row['http_status']) is int and 100<=row['http_status']<=599 and not 300<=row['http_status']<400,'runtime_view_redirect_rejected')
        return row,raw
    async def offer(self,args,runtime,source,*,_host_current=None):
        need(set(args)=={'service_id','workspace_sha256'} and re.fullmatch('[0-9a-f]{32}',args['service_id']) and HEX.fullmatch(args['workspace_sha256']),'runtime_view_exact_service_required')
        if _host_current is None:self.pending=None;self.event=None;self.opening=None
        else:need(callable(_host_current) and _host_current() and self.pending is None,'runtime_view_background_owner_changed')
        await self.live(runtime,args)
        if _host_current is not None:need(_host_current() and self.pending is None,'runtime_view_background_owner_changed')
        row,raw=await self.fetch(runtime,args)
        if _host_current is not None:need(_host_current() and self.pending is None,'runtime_view_background_owner_changed')
        need(row['content_type'].split(';')[0]=='text/html' and row['http_status']==200,'runtime_view_HTML_required')
        prompt='Your app is running and ready to view in Artifacts. Would you like me to open it?'
        self.pending=dict(operation_id=uuid.uuid4().hex,expected_digest=hashlib.sha256(json.dumps(args,sort_keys=True).encode()).hexdigest(),args=dict(args),birth=self.birth(row),source=source,context=self.identity(),expires=self.clock()+120,prompt=prompt)
        return dict(status='needs_confirmation',reply=prompt,execution_started=False,preview_opened=False,functionality_verified=False)
    async def respond(self,raw,history,*,captured_during_review,runtime_factory,_host_acceptance=None):
        p=self.pending
        if not self.valid_pending(p):self.pending=None;return 'That app view offer expired or the conversation changed. Nothing was opened.'
        intent=confirmation_intent(raw)
        if _host_acceptance is not None:_host_acceptance.check(self,p,raw,history);intent='affirmative'
        if captured_during_review or intent=='question' or not prompt_delivered(history,p['prompt']) or history[-2].get('partial_output'):return p['prompt']
        need(intent=='affirmative','runtime_view_separate_confirmation_required')
        self.pending=None;self.event=None
        runtime,source=runtime_factory();need(source==p['source'],'runtime_view_source_changed')
        await self.live(runtime,p['args']);row,_=await self.fetch(runtime,p['args'])
        need(self.birth(row)==p['birth'] and self.valid_pending(p),'runtime_view_identity_changed')
        self.event={**p,'open_id':uuid.uuid4().hex,'expires':self.clock()+30}
        return 'Opening your running app in Artifacts. You can keep talking while it runs.'
    def notification(self):
        p=self.event
        if not self.valid_pending(p):self.event=None;return None
        return dict(open_id=p['open_id'],conversation_id=p['context'][0],revision=p['context'][1],path='Running app',kind='runtime')
    async def open(self,open_id,runtime,source):
        p=self.event;self.event=None;self.opening=p
        need(self.valid_pending(p) and p['open_id']==open_id and p['source']==source,'runtime_view_current_once_confirmation_required')
        await self.live(runtime,p['args']);row,raw=await self.fetch(runtime,p['args'])
        need(self.birth(row)==p['birth'] and self.valid_pending(p),'runtime_view_identity_changed')
        from me3_assistant.artifact_delivery import StaticHTML,PREVIEW_CSP
        files={'index.html':raw};assets=Assets();assets.feed(raw.decode('utf-8'));need(len(assets.paths)<=8,'runtime_view_asset_count')
        for path in dict.fromkeys(assets.paths):
            asset,body=await self.fetch(runtime,p['args'],path='/'+path)
            need(self.birth(asset)==p['birth'] and asset['http_status']==200,'runtime_view_asset_identity')
            files[path]=body
        parser=StaticHTML('index.html',files);parser.feed(raw.decode('utf-8'));parser.close()
        need(parser.blocked_scripts==0 and parser.blocked_assets==0,'runtime_view_asset_unavailable')
        nonce=uuid.uuid4().hex
        bridge=BRIDGE.replace('__NONCE__',nonce)
        html='<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="'+escape(PREVIEW_CSP,quote=True)+'"><script>'+bridge+'</script>'+''.join(parser.output)
        need(len(html.encode())<=262144 and self.valid_pending(p) and self.opening is p,'runtime_view_document_bound')
        self.inactive=None
        self.active=dict(args=p['args'],birth=p['birth'],path=row['path'],source=source,conversation_id=self.context.conversation_id,view_id=uuid.uuid4().hex,nonce=nonce,expires=self.clock()+600,requests=0,seen=set(),document=html)
        self.opening=None
        self.context.artifact_preview.active=None
        return dict(ok=True,view_id=self.active['view_id'],nonce=nonce,conversation_id=self.context.conversation_id)
    async def document(self,view_id,runtime,source):
        p=self.active
        need(self.valid_active(p,source) and p['view_id']==view_id and isinstance(p.get('document'),str),'runtime_view_once_document_required')
        document=p.pop('document')
        proof,_=await self.fetch(runtime,p['args'])
        need(self.birth(proof)==p['birth'] and self.valid_active(p,source) and self.active is p,'runtime_view_document_identity_changed')
        return document
    def metadata(self):
        p=self.active
        if not p or p['conversation_id']!=self.context.conversation_id or self.clock()>=p['expires']:return None
        return dict(service_id=p['args']['service_id'],workspace_manifest_sha256=p['args']['workspace_sha256'],path=p.get('path'),last_known_state='running',cached=True,live_status_verified=False,authority=False,policy='This is only the previously opened app identity. Use guarded manage_workspace_runtime status/stop for current state or requested shutdown; opening/closing its frame does not start/stop the backend.')
    async def relay(self,view_id,request_id,request,runtime,source):
        p=self.active;need(self.valid_active(p,source) and p['view_id']==view_id,'runtime_view_lease_expired')
        need(type(request_id) is int and 0<request_id<=9007199254740991 and request_id not in p['seen'] and len(p['seen'])<128,'runtime_view_once_request_required')
        p['seen'].add(request_id)  # consume before validation/dispatch; uncertainty never authorizes replay
        need(set(request)<={'method','path','body_base64','content_type'} and set(request)>={'method','path'} and request['method'] in {'GET','POST'},'runtime_view_request_fields')
        path=request_path(request['path']);raw=base64.b64decode(request.get('body_base64',''),validate=True);need(len(raw)<=16384 and p['requests']<128,'runtime_view_request_bound')
        need(request['method']=='POST' or not raw,'runtime_view_GET_body')
        p['requests']+=1
        try:
            row,body=await self.fetch(runtime,p['args'],method=request['method'],path=path,body=raw,content_type=request.get('content_type'))
            need(self.birth(row)==p['birth'] and self.valid_active(p,source) and self.active is p,'runtime_view_identity_changed')
            return dict(ok=True,http_status=row['http_status'],content_type=row['content_type'],body_base64=base64.b64encode(body).decode())
        except BaseException:self.active=None;raise
    async def poll(self,view_id,runtime,source):
        p=self.active;context=self.identity()
        if not self.valid_active(p,source) or p['view_id']!=view_id:return dict(ok=False,open=False,notice='The app view was closed. Its backend status has not been confirmed.')
        try:
            row=await runtime.status(p['args']['service_id'])
            live=row.get('status')=='running' and row.get('outcome_verified') is True and row.get('workspace_manifest_sha256')==p['args']['workspace_sha256'] and row.get('owned_namespace_closed') is False
            if live:
                proof,_=await self.fetch(runtime,p['args']);live=self.birth(proof)==p['birth']
            if live and self.valid_active(p,source):return dict(ok=True,open=True)
            closed=row.get('outcome_verified') is True and row.get('owned_namespace_closed') is True and row.get('status') in {'stopped','expired','failed'}
        except Exception:closed=False
        # A late poll must not clear a replacement view or certify its closure.
        if self.active is not p or context!=self.identity() or not self.valid_active(p,source):return dict(ok=False,open=False,notice='The previous app view is no longer current.')
        closed=closed and row.get('service_id')==p['args']['service_id'] and row.get('workspace_manifest_sha256')==p['args']['workspace_sha256']
        self.inactive=None
        if closed:
            now=self.clock()
            self.inactive=dict(identity=dict(kind='runtime',view_id=p['view_id'],target=p['args']['service_id'],conversation_id=p['conversation_id'],source_revision=p['source'],backend_stopped=False,cached=True),context=self.identity(),closed_at=now,expires=min(p['expires'],now+30))
        self.active=None
        return dict(ok=True,open=False,backend_closed_verified=closed,notice='The app stopped and its view was closed.' if closed else 'The app view was closed because its current backend status could not be verified.')

BRIDGE=r'''(()=>{'use strict';const nonce='__NONCE__',pending=new Map();let sequence=0;
window.fetch=(input,options={})=>{const u=new URL(String(input),'https://me3-app.invalid/');if(u.origin!=='https://me3-app.invalid'||u.hash||options.headers&&Object.keys(options.headers).some(k=>k.toLowerCase()!=='content-type'))return Promise.reject(new Error('Only this app GET/POST is supported.'));const method=(options.method||'GET').toUpperCase();if(!['GET','POST'].includes(method))return Promise.reject(new Error('Unsupported method'));const text=options.body||'';if(typeof text!=='string'||new TextEncoder().encode(text).length>16384)return Promise.reject(new Error('Body too large'));const bytes=new TextEncoder().encode(text);let binary='';for(const b of bytes)binary+=String.fromCharCode(b);const id=++sequence;return new Promise((resolve,reject)=>{const timer=setTimeout(()=>{pending.delete(id);reject(new Error('App request timed out; it was not retried.'));},10000);pending.set(id,{resolve,reject,timer});parent.postMessage({kind:'me3-app-request',nonce,id,request:{method,path:u.pathname+u.search,body_base64:btoa(binary),content_type:method==='POST'?(options.headers?.['Content-Type']||options.headers?.['content-type']||'text/plain'):null}},'*');});};
addEventListener('message',e=>{if(e.source!==parent||e.data?.kind!=='me3-app-response'||e.data.nonce!==nonce)return;const p=pending.get(e.data.id);if(!p)return;pending.delete(e.data.id);clearTimeout(p.timer);if(!e.data.ok){p.reject(new Error('App request unavailable; no retry.'));return;}const raw=atob(e.data.body_base64);const body=Uint8Array.from(raw,c=>c.charCodeAt(0));p.resolve(new Response(body,{status:e.data.http_status,headers:{'Content-Type':e.data.content_type}}));});
window.XMLHttpRequest=undefined;window.WebSocket=undefined;window.EventSource=undefined;window.Worker=undefined;navigator.sendBeacon=()=>false;
addEventListener('click',e=>{if(e.target.closest?.('a'))e.preventDefault();},true);addEventListener('submit',e=>e.preventDefault(),true);
})();'''
