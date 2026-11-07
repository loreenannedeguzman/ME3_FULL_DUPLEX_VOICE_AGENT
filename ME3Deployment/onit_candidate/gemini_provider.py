"""Explicit Gemini native-tool transport. No retries, fallback or child credentials."""
import asyncio,copy,json,math,socket,time
from pathlib import Path
import aiohttp
from dotenv import dotenv_values
from onit_candidate.protocol import CandidateError,native_payload
from onit_candidate.reasoning_profile import GEMINI_MODEL
from me3_assistant.admission import Admission,duration,estimated_tokens
from me3_assistant.groq_backend import ProviderError
from me3_assistant.voice_policy import require_session_open
ENDPOINT='https://generativelanguage.googleapis.com/v1beta/openai/chat/completions'
def admission_path(root):return Path(root)/('evidence/assistant/provider-admission/google-'+GEMINI_MODEL+'.json')
def key(root=None):
    from task_mcp.client import ROOT
    p=Path(root or ROOT)/'.env'
    if p.is_symlink() or not p.is_file():raise ValueError('private_google_key_file_required')
    value=dotenv_values(p,encoding='utf-8-sig',interpolate=False).get('GOOGLE_API_KEY')
    if not isinstance(value,str) or not value or any(c.isspace() for c in value):raise ValueError('google_credential_required')
    return value
def rejection(raw):
    result=dict(error_code='unknown',limit_scope='unknown',body_retained=False);wait=None
    try:
        error=json.loads(raw)['error'];status=error.get('status')
        # Allowlisted identifiers only: neither private error prose nor bodies.
        for field in ('status','type','code'):
            value=error.get(field)
            if isinstance(value,str) and value in {'RESOURCE_EXHAUSTED','INVALID_ARGUMENT','PERMISSION_DENIED','UNAUTHENTICATED','NOT_FOUND','UNAVAILABLE','INTERNAL','DEADLINE_EXCEEDED','api_error','service_unavailable','deadline_exceeded'}:
                result['error_code']=value;break
        scopes=set()
        for d in error.get('details',[]):
            if d.get('@type')=='type.googleapis.com/google.rpc.RetryInfo':wait=duration(d.get('retryDelay'))
            if d.get('@type')=='type.googleapis.com/google.rpc.QuotaFailure':
                for v in d.get('violations',[]):
                    identifier=str(v.get('quotaId','')).lower()
                    for marker,label in [('tokensperday','tokens_per_day'),('requestsperday','requests_per_day'),('tokensperminute','tokens_per_minute'),('requestsperminute','requests_per_minute')]:
                        if marker in identifier:scopes.add(label)
        if len(scopes)==1:result['limit_scope']=next(iter(scopes))
        elif scopes:result['limit_scope']='multiple_named_limits'
    except (ValueError,KeyError,TypeError,AttributeError,RecursionError):pass
    return result,wait
class Transport:
    provider='google';model=GEMINI_MODEL
    def __init__(self,emit,*,root=None,session=None,credential=None,admission=None,output=None):
        from task_mcp.client import ROOT
        from onit_candidate.output_quota import OutputQuota
        self.root=Path(root or ROOT);self.emit=emit;self.key=credential if credential is not None else key(self.root)
        self.session=session;self.owned=session is None;self.active=False;self.stop_requested=None;self.before_capacity_wait=None
        self.admission=admission or Admission(admission_path(self.root));self.output=output or OutputQuota(self.root/('evidence/assistant/provider-admission/google-'+GEMINI_MODEL+'-output.sqlite3'))
        self.signature_scope=None;self.signatures={}
        self.network_stage='idle';self.request_started=None
    def network_trace(self):
        trace=aiohttp.TraceConfig()
        for signal,stage in [('on_dns_resolvehost_start','DNS'),('on_dns_resolvehost_end','connecting'),('on_connection_create_start','connecting'),('on_connection_create_end','connection_ready'),('on_connection_reuseconn','connection_ready'),('on_request_headers_sent','waiting_for_response_headers')]:
            async def observe(session,context,params,phase=stage):
                self.network_stage=phase
                self.emit('onit_provider_network_stage',provider='google',stage=phase,elapsed_seconds=round(time.monotonic()-self.request_started,3) if self.request_started is not None else None,credentials_retained=False,text_retained=False)
            getattr(trace,signal).append(observe)
        return trace
    def payload(self,raw):
        value=native_payload(raw,expected_model=self.model);value.pop('include_reasoning',None);value.pop('parallel_tool_calls',None)
        value['reasoning_effort']='minimal';value['max_tokens']=value.pop('max_completion_tokens');value['messages']=copy.deepcopy(value['messages'])
        if self.signature_scope is not self.stop_requested:self.signatures.clear();self.signature_scope=self.stop_requested
        for message in value['messages']:
            for call in message.get('tool_calls') or []:
                saved=self.signatures.get(call.get('id'))
                if saved is not None:call['extra_content']=copy.deepcopy(saved)
        return value
    def ensure_session(self):
        if self.session is None:
            connector=aiohttp.TCPConnector(family=socket.AF_INET,limit=1,force_close=True,keepalive_timeout=None,ttl_dns_cache=120)
            self.session=aiohttp.ClientSession(connector=connector,timeout=aiohttp.ClientTimeout(total=60,connect=10,sock_read=45),trust_env=True,trace_configs=[self.network_trace()])
        if self.session.closed:raise ValueError('reasoning_transport_closed')
        return self.session
    def wire_request(self,payload):return ENDPOINT,payload,{'Authorization':'Bearer '+self.key}
    def decode_response(self,data):return data
    async def request(self,role,*,json:dict,capacity_wait_seconds=0):
        if role!='reasoning' or self.active:raise ValueError('single_reasoning_request_required')
        require_session_open(self.stop_requested);payload=self.payload(json);estimate=estimated_tokens(role,dict(json={**payload,'max_completion_tokens':payload['max_tokens']}))
        endpoint,wire,auth=self.wire_request(payload)
        self.admission.cap=payload['max_tokens']
        self.active=True;ticket=None;output_ticket=None;dispatched=False;actual=None;self.request_started=time.monotonic();self.network_stage='admission'
        try:
            delay=self.admission.capacity_delay(role,estimate,min(60,capacity_wait_seconds))
            if delay:
                if self.before_capacity_wait:self.before_capacity_wait(delay)
                self.emit('groq_capacity_wait_start',role=role,provider=self.provider,seconds=delay,wire_attempts=0,automatic_retry=False)
                await asyncio.sleep(delay);self.emit('groq_capacity_wait_end',role=role,provider=self.provider,wire_attempts=0,automatic_retry=False)
            require_session_open(self.stop_requested)
            ticket=self.admission.admit(role,estimate);output_ticket=self.output.reserve(payload['max_tokens'])
            session=self.ensure_session();require_session_open(self.stop_requested)
            self.network_stage='connecting'
            self.emit('onit_provider_request_dispatch',role=role,provider=self.provider,model=self.model,wire_attempt=1,automatic_retry=False);dispatched=True
            async with session.post(endpoint,json=wire,headers=auth,allow_redirects=False) as response:
                self.network_stage='response_headers'
                self.emit('onit_provider_response_headers',role=role,provider=self.provider,status=response.status,wire_attempt=1)
                chunks=[];size=0;self.network_stage='reading_response_body'
                while True:
                    part=await response.content.read(min(16384,524289-size))
                    if not part:break
                    chunks.append(part);size+=len(part)
                    if size>524288:raise CandidateError('provider_response_size')
                raw=b''.join(chunks);headers=dict(response.headers)
                if response.status!=200:
                    meta,wait=rejection(raw)
                    if wait is not None:headers['retry-after']=str(wait)
                    self.admission.observe(role,ticket,headers,response.status)
                    if response.status==429:self.emit('onit_provider_capacity_rejection',role=role,provider=self.provider,status=429,**meta,retry_after_seconds=duration(headers.get('retry-after')),automatic_retry=False)
                    else:self.emit('onit_provider_rejection',role=role,provider=self.provider,status=response.status,**meta,automatic_retry=False)
                    raise ProviderError(response.status,meta['error_code'])
                self.admission.observe(role,ticket,headers,200);require_session_open(self.stop_requested)
                data=self.decode_response(__import__('json').loads(raw))
                if not isinstance(data,dict) or data.get('model') not in {self.model,'models/'+self.model}:raise CandidateError('unexpected_response_model')
                data['model']=self.model
                usage=data.get('usage',{});safe={k:usage[k] for k in ['prompt_tokens','completion_tokens','total_tokens'] if type(usage.get(k)) is int and 0<=usage[k]<=1000000};actual=safe.get('completion_tokens')
                self.emit('onit_provider_token_usage',provider=self.provider,role=role,**safe)
                for choice in data.get('choices',[]):
                    for call in choice.get('message',{}).get('tool_calls') or []:
                        extra=call.get('extra_content')
                        if isinstance(extra,dict) and (isinstance(extra.get('google',{}).get('thought_signature'),str) or isinstance(extra.get('google',{}).get('native_call_id'),str)):
                            if len(self.signatures)>=64:raise CandidateError('signature_state_bound')
                            self.signatures[call['id']]=copy.deepcopy(extra)
                self.network_stage='complete';return data
        except BaseException as exc:
            self.emit('onit_provider_request_failed',provider='google',stage=self.network_stage,error_type=type(exc).__name__,elapsed_seconds=round(time.monotonic()-self.request_started,3),dispatched=dispatched,automatic_retry=False,credentials_retained=False,text_retained=False)
            raise
        finally:
            try:
                if output_ticket:self.output.finish(output_ticket,actual,dispatched)
            finally:
                try:
                    if ticket:self.admission.finish(role,ticket)
                finally:self.active=False
    async def close(self):
        self.signatures.clear()
        if self.owned and self.session is not None:await self.session.close()
