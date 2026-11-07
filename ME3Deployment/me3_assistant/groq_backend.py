"""Unselected Groq adapters. No client creation or requests on import."""
import asyncio,json,math,os,re,sys,socket
from types import SimpleNamespace
import aiohttp
from google.genai import types
from speech_mcp.contracts import pcm_to_wav
from me3_assistant.reasoning import PROMPT
from me3_assistant.admission import Admission,AdmissionDenied,estimated_tokens,project_admission

ASR_MODEL='whisper-large-v3-turbo';TEXT_MODEL='openai/gpt-oss-20b'
ENDPOINTS={'asr':'https://api.groq.com/openai/v1/audio/transcriptions','reasoning':'https://api.groq.com/openai/v1/chat/completions'}

def connection_trace(emit):
    """Public aiohttp lifecycle hooks; never inspect headers, URLs or bodies.

    The client connect timer can cancel the connector's equal3s deadline first.
    Record the actual unfinished stage rather than treating that cancellation
    event as evidence of a user cancel, TLS fault or provider response timeout.
    """
    trace=aiohttp.TraceConfig()
    def stage(signal,event,value):
        async def callback(session,context,params):
            context.me3_network_stage=value;emit(event)
        getattr(trace,signal).append(callback)
    for signal,event,value in [
        ('on_request_start','groq_network_request_start','starting'),
        ('on_connection_queued_start','groq_network_pool_wait_start','pool_wait'),
        ('on_connection_queued_end','groq_network_pool_wait_end','connection'),
        ('on_connection_create_start','groq_network_connection_start','connection'),
        ('on_dns_resolvehost_start','groq_network_dns_start','dns'),
        ('on_dns_resolvehost_end','groq_network_dns_end','tcp_tls'),
        ('on_dns_cache_hit','groq_network_dns_cache_hit','tcp_tls'),
        ('on_connection_create_end','groq_network_connection_ready','ready'),
        ('on_connection_reuseconn','groq_network_connection_reused','reused'),
        ('on_request_headers_sent','groq_network_http_headers_sent','http_response')]:stage(signal,event,value)
    async def failed(session,context,params):
        emit('groq_network_request_error',stage=getattr(context,'me3_network_stage','unknown'),error_type=type(params.exception).__name__,automatic_retry=False)
    trace.on_request_exception.append(failed)
    return trace

class ProviderError(ValueError):
    def __init__(self,code,provider_error_code=None):self.code=code;self.provider_error_code=provider_error_code;super().__init__('candidate_provider_http_error')

def error_metadata(raw,tools,schema=None):
    """No provider prose, generated arguments, credentials or private text escapes."""
    safe={'provider_error_code':'unclassified','failed_call_registered':None,'failed_plan_steps':None,'failed_schema_validator':None}
    try:
        value=json.loads(raw);error=value.get('error',{});code=error.get('code')
        if code in {'tool_use_failed','json_validate_failed','invalid_request_error','invalid_function_parameters','context_length_exceeded','rate_limit_exceeded'}:safe['provider_error_code']=code
        generated=error.get('failed_generation','')
        if code=='json_validate_failed':
            safe['failed_json_kind']='oversized' if isinstance(generated,str) and len(generated)>16384 else 'absent'
            if isinstance(generated,str) and generated and len(generated)<=16384:
                try:
                    failed=json.loads(generated)
                    safe['failed_json_kind']='object' if isinstance(failed,dict) else 'other_json'
                    if schema is not None:
                        from jsonschema import Draft202012Validator
                        first=next(Draft202012Validator(schema).iter_errors(failed),None)
                        safe['failed_json_schema_valid']=first is None
                        if first and first.validator in {'type','required','enum','additionalProperties','minItems','maxItems','anyOf'}:safe['failed_schema_validator']=first.validator
                        if first is not None and first.validator=='required' and isinstance(first.instance,dict):
                            # Only fixed envelope fields; never dynamic MCP names,
                            # property paths, values, provider prose or transcripts.
                            fixed={'speech','calls','name','arguments_json'}
                            missing=[k for k in first.validator_value if k in fixed and k not in first.instance]
                            safe['failed_missing_contract_fields']=sorted(missing)
                            path=list(first.absolute_schema_path)
                            safe['failed_contract_location']=('decision' if path==['required'] else 'operation' if path==['properties','calls','items','required'] else 'other')
                except (ValueError,TypeError,RecursionError):safe['failed_json_kind']='malformed'
            return safe
        if not isinstance(generated,str) or len(generated)>16384:return safe
        found=re.fullmatch(r'\s*<function=([A-Za-z_][A-Za-z_0-9]{0,63})>(.*?)</function>\s*',generated,re.S)
        if found:name=found[1];args=json.loads(found[2])
        else:
            call=json.loads(generated);call=call.get('function',call);name=call.get('name');args=call.get('arguments')
            if isinstance(args,str):args=json.loads(args)
        declarations={t['function']['name']:t['function']['parameters'] for t in tools}
        safe['failed_call_registered']=name in declarations
        if name=='run_goal_steps' and isinstance(args,dict) and isinstance(args.get('steps'),list):safe['failed_plan_steps']=min(len(args['steps']),7)
        if name in declarations:
            from jsonschema import Draft202012Validator
            first=next(Draft202012Validator(declarations[name]).iter_errors(args),None)
            if first and first.validator in {'anyOf','type','required','enum','additionalProperties','minimum','maximum','minItems','maxItems'}:safe['failed_schema_validator']=first.validator
    except (ValueError,TypeError,AttributeError,KeyError):pass
    return safe

GROQ_CONNECTION_FAMILY=socket.AF_INET
ASR_REQUEST_SECONDS=30

def groq_connector():
    # Pi A-only NSS resolution/TLS passed while combined-family readiness
    # failed. Scope family selection to this provider; keep system resolver,
    # default DNS-cache TTL, hostname/SNI and certificate verification.
    from pathlib import Path
    cfg=json.loads((Path(__file__).resolve().parents[1]/'config/core_agent.json').read_bytes())
    keepalive=cfg['http_keepalive_seconds'];dns=cfg['dns_cache_seconds']
    if type(keepalive) is not int or type(dns) is not int or not 15<=keepalive<=120 or not 10<=dns<=300:
        raise ValueError('invalid_connection_cache_bounds')
    return aiohttp.TCPConnector(family=GROQ_CONNECTION_FAMILY,limit=1,keepalive_timeout=keepalive,ttl_dns_cache=dns)

class Transport:
    def __init__(self,emit,*,key=None,session=None,opt_in=False,admission=None):
        self.emit=emit;self.key=key or os.getenv('GROQ_API_KEY');self.session=session;self.owned=session is None;self.active=False
        if not self.key:raise ValueError('groq_credential_required')
        if self.owned and opt_in is not True:raise ValueError('explicit_groq_backend_required')
        self.stop_requested=None;self.before_capacity_wait=None
        self.admission=admission if admission is not None else (project_admission() if self.owned else Admission())
    def ensure_session(self,role):
        if role not in ENDPOINTS:raise ValueError('unapproved_candidate_endpoint')
        if self.session is None:
            from me3_assistant.transport import bind_connection_deadline,ASR_CONNECT_SECONDS,ASR_ADDRESS_SECONDS,REASONING_CONNECT_SECONDS
            connect_seconds=REASONING_CONNECT_SECONDS if role=='reasoning' else ASR_CONNECT_SECONDS
            self.emit('groq_connection_policy',address_family='IPv4',system_network_settings_changed=False,pinned_IP=False,automatic_retry=False)
            self.session=aiohttp.ClientSession(connector=groq_connector(),timeout=aiohttp.ClientTimeout(total=30,connect=connect_seconds),trust_env=True,trace_configs=[connection_trace(self.emit)])
            # After the idle socket expires, allow TCP/TLS time to establish
            # each ASR address. The old1s guard exhausted two addresses in2s
            # despite the10s outer budget. This remains pre-HTTP only: no audio
            # upload replay, extra request, DNS override or TLS relaxation.
            bind_connection_deadline(SimpleNamespace(owned_session=self.session,emit=self.emit),connect_seconds,**({'address_seconds':min(ASR_ADDRESS_SECONDS,connect_seconds/2)} if role=='asr' else {}))
        if getattr(self.session,'closed',False):raise ValueError('groq_transport_closed')
        return self.session
    async def request(self,role,*,capacity_wait_seconds=0,**payload):
        # ASR resolution and its single HTTP attempt share the existing total
        # request budget. A fresh DNS lookup must not consume the shorter TCP/
        # TLS connect timer after an idle socket and cache entry expire.
        if self.owned and role=='asr':
            async with asyncio.timeout(ASR_REQUEST_SECONDS):
                return await self._request(role,capacity_wait_seconds=capacity_wait_seconds,**payload)
        return await self._request(role,capacity_wait_seconds=capacity_wait_seconds,**payload)
    async def _request(self,role,*,capacity_wait_seconds=0,**payload):
        from me3_assistant.voice_policy import require_session_open
        require_session_open(self.stop_requested)
        if role not in ENDPOINTS:raise ValueError('unapproved_candidate_endpoint')
        if self.active:raise ValueError('candidate_request_already_active')
        self.active=True;ticket=None
        try:
            estimate=estimated_tokens(role,payload)
            if capacity_wait_seconds:
                delay=self.admission.capacity_delay(role,estimate,capacity_wait_seconds)
                if delay:
                    if self.before_capacity_wait is not None:
                        try:self.before_capacity_wait(delay)
                        except AdmissionDenied as exc:
                            self.emit('groq_admission_denied',role=role,reason=exc.reason,wire_attempts=0,automatic_retry=False)
                            raise
                    self.emit('groq_capacity_wait_start',role=role,seconds=round(delay,3),wire_attempts=0,automatic_retry=False)
                    try:await asyncio.sleep(delay)
                    except asyncio.CancelledError:
                        self.emit('groq_capacity_wait_cancelled',role=role,wire_attempts=0,automatic_retry=False);raise
                    self.emit('groq_capacity_wait_end',role=role,wire_attempts=0,automatic_retry=False)
            require_session_open(self.stop_requested)
            if self.owned and role=='asr':
                from me3_assistant.worker_readiness import resolve_for_request
                await resolve_for_request(self)
                require_session_open(self.stop_requested)
            try:ticket=self.admission.admit(role,estimate)
            except AdmissionDenied as exc:
                self.emit('groq_admission_denied',role=role,reason=exc.reason,wire_attempts=0,automatic_retry=False)
                raise
            self.emit('groq_admission_granted',role=role,estimated_tokens=estimate,estimate_is_exact=False,request_header_scope='RPD',token_header_scope='TPM')
            self.ensure_session(role)
            require_session_open(self.stop_requested)
            self.emit('groq_request_dispatch',role=role,wire_attempt=1)
            async with self.session.post(ENDPOINTS[role],headers={'Authorization':'Bearer '+self.key},allow_redirects=False,**payload) as response:
                self.emit('groq_response_headers',role=role,status=response.status,wire_attempt=1)
                headers=getattr(response,'headers',{})
                self.admission.observe(role,ticket,headers,response.status)
                limits={k:int(headers[k]) for k in ('x-ratelimit-limit-requests','x-ratelimit-remaining-requests','x-ratelimit-limit-tokens','x-ratelimit-remaining-tokens') if str(headers.get(k,'')).isdigit() and len(str(headers[k]))<=10}
                if limits:self.emit('groq_reported_rate_limits',role=role,**limits)
                if response.status!=200:
                    if role=='reasoning' and response.status==400:
                        # Only a400planning rejection needs classification. Read
                        # bounded bytes transiently; never log body/prose/values.
                        chunks=[];size=0
                        try:
                            while size<=131072:
                                part=await response.content.read(min(16384,131073-size))
                                if not part:break
                                chunks.append(part);size+=len(part)
                            meta=error_metadata(b''.join(chunks),payload.get('json',{}).get('tools',[]),payload.get('json',{}).get('response_format',{}).get('json_schema',{}).get('schema')) if size<=131072 else error_metadata(b'',[])
                        except (ValueError,OSError,asyncio.TimeoutError):meta=error_metadata(b'',[])
                        self.emit('groq_planning_rejection',role=role,**meta,body_retained=False,argument_values_retained=False,automatic_retry=False)
                        raise ProviderError(response.status,meta['provider_error_code'])
                    raise ProviderError(response.status)
                chunks=[];size=0
                while True:
                    part=await response.content.read(min(16384,131073-size))
                    if not part:break
                    chunks.append(part);size+=len(part)
                    if size>131072:raise ValueError('candidate_response_too_large')
                data=json.loads(b''.join(chunks))
                if not isinstance(data,dict):raise ValueError('invalid_candidate_response')
                usage=data.get('usage')
                if isinstance(usage,dict):self.emit('groq_reported_token_usage',role=role,**{k:usage[k] for k in ('prompt_tokens','completion_tokens','total_tokens') if type(usage.get(k)) is int and 0<=usage[k]<=1000000})
                return data
        finally:
            pending=sys.exc_info()[0] is not None
            try:
                if ticket is not None:
                    try:self.admission.finish(role,ticket)
                    except Exception:
                        self.emit('groq_admission_cleanup_failed',role=role,automatic_retry=False)
                        if not pending:raise
            finally:self.active=False
    async def close(self):
        if self.owned and self.session is not None:await self.session.close()

def validate_pcm(pcm,maximum=480000):
    if not isinstance(pcm,bytes) or not 0<len(pcm)<=maximum or len(pcm)%2:raise ValueError('invalid_candidate_pcm')

def transcript(data,boundary=None,duration=None):
    text=data.get('text')
    if not isinstance(text,str) or len(text)>4000 or '\0' in text:raise ValueError('invalid_candidate_transcript')
    if boundary is None:return {'text':text.strip()}
    words=data.get('words');previous=[];current=[];end_before=0
    if not isinstance(words,list) or len(words)>256:raise ValueError('split_word_timestamps_required')
    for w in words:
        if not isinstance(w,dict):raise ValueError('invalid_candidate_word')
        token=w.get('word');start=w.get('start');end=w.get('end')
        if not isinstance(token,str) or not token.strip() or len(token)>100 or any(type(t) not in (int,float) or not math.isfinite(t) for t in (start,end)) or not 0<=start<=end<=duration or start<end_before:raise ValueError('invalid_candidate_word')
        end_before=end
        if start<boundary<end or start==end==boundary:raise ValueError('split_boundary_uncertain')
        (previous if end<=boundary else current).append(token.strip())
    # Timestamp splitting must account for the entire returned transcript.
    normalize=lambda s:re.findall(r'\w+',s.casefold())
    if normalize(' '.join(previous+current))!=normalize(text):raise ValueError('incomplete_candidate_word_alignment')
    return {'previous_text':' '.join(previous),'text':' '.join(current)}

def english_language_status(value):
    """Interpret language metadata, never transcript contents or instructions."""
    if value is None:return 'not_reported'
    if not isinstance(value,str):return 'invalid_type'
    if len(value)>64:return 'unrecognized'
    label=value.strip().casefold()
    if label in {'english','eng'} or re.fullmatch(r'en(?:[-_][a-z]{4})?(?:[-_](?:[a-z]{2}|[0-9]{3}))?',label):return 'english'
    return 'conflicting_or_unrecognized'

class GroqASR:
    def __init__(self,emit,*,transport=None,language='en'):
        if language is not None and (not isinstance(language,str) or re.fullmatch('[a-z]{2}',language) is None):raise ValueError('invalid_asr_language')
        self.language=language
        from me3_assistant.backends import groq_key
        self.emit=emit;self.transport=transport or Transport(emit,key=groq_key(),opt_in=True)
    async def transcribe(self,pcm,profile,previous=None,audio_format='wav'):
        if profile!='low' or audio_format not in {'wav','flac'}:raise ValueError('invalid_candidate_asr_configuration')
        validate_pcm(pcm,3840000)
        if previous is not None:validate_pcm(previous,3840000)
        data=(previous or b'')+pcm;validate_pcm(data,3840000)
        form=aiohttp.FormData();form.add_field('model',ASR_MODEL);form.add_field('response_format','verbose_json');form.add_field('temperature','0');form.add_field('timestamp_granularities[]','word');form.add_field('file',pcm_to_wav(data),filename='clip.wav',content_type='audio/wav')
        if self.language is not None:form.add_field('language',self.language)
        self.emit('groq_asr_language_policy',language=self.language or 'auto',automatic_detection=self.language is None)
        # One combined file and one POST. Separate clips are recovered only from
        # validated word times; an ambiguous boundary fails without extra calls.
        result=await self.transport.request('asr',data=form)
        if self.language=='en':
            status=english_language_status(result.get('language'))
            self.emit('groq_asr_language_validation',expected='en',reported_language_status=status,text_retained=False)
            if status not in {'english','not_reported'}:
                self.emit('groq_asr_language_mismatch',expected='en',reported_language_status=status,text_retained=False,automatic_retry=False)
                raise ValueError('asr_language_mismatch')
        return transcript(result,len(previous)/32000 if previous is not None else None,len(data)/32000)
    async def prepare_connection(self):
        from me3_assistant.worker_readiness import prepare_connection
        return await prepare_connection(self.transport)
    async def close(self):await self.transport.close()

def messages(history,continuation):
    out=[{'role':'system','content':PROMPT}]
    for item in history:out.append({'role':'user' if item['type']=='user_input' else 'assistant','content':' '.join(p['text'] for p in item['content'])})
    pending=[]
    for content in continuation:
        parts=content.parts or []
        if content.role=='model':
            calls=[];texts=[]
            for p in parts:
                if p.function_call:
                    c=p.function_call
                    if not c.id or any(x[0]==c.id for x in pending):raise ValueError('candidate_tool_identity_required')
                    calls.append({'id':c.id,'type':'function','function':{'name':c.name,'arguments':json.dumps(dict(c.args or {}),separators=(',',':'))}});pending.append((c.id,c.name))
                elif p.text and not p.thought:texts.append(p.text)
            out.append({'role':'assistant','content':' '.join(texts) or None,**({'tool_calls':calls} if calls else {})})
        else:
            for p in parts:
                if p.function_response:
                    r=p.function_response;matches=[x for x in pending if x[1]==r.name and (not r.id or x[0]==r.id)]
                    if len(matches)!=1:raise ValueError('candidate_tool_response_ambiguous')
                    match=matches[0];pending.remove(match);out.append({'role':'tool','tool_call_id':match[0],'content':json.dumps(dict(r.response))})
                elif p.text:out.append({'role':'user','content':p.text})
    if pending:raise ValueError('candidate_tool_response_missing')
    return out

def decision(data):
    choices=data.get('choices')
    if not isinstance(choices,list) or len(choices)!=1 or not isinstance(choices[0],dict):raise ValueError('invalid_candidate_decision')
    message=choices[0].get('message')
    if not isinstance(message,dict):raise ValueError('invalid_candidate_decision')
    text=message.get('content') or '';calls=message.get('tool_calls') or []
    if not isinstance(text,str) or len(text)>40000 or not isinstance(calls,list) or len(calls)>3:raise ValueError('invalid_candidate_decision')
    parts=[types.Part(text=text)] if text else [];normalized=[];identities=set()
    for c in calls:
        if not isinstance(c,dict) or c.get('type')!='function' or not isinstance(c.get('id'),str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',c['id']):raise ValueError('invalid_candidate_tool_call')
        if c['id'] in identities:raise ValueError('invalid_candidate_tool_call')
        identities.add(c['id'])
        f=c.get('function')
        if not isinstance(f,dict) or not isinstance(f.get('name'),str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}',f['name']) or not isinstance(f.get('arguments'),str) or len(f['arguments'])>16384:raise ValueError('invalid_candidate_tool_call')
        args=json.loads(f['arguments'])
        if not isinstance(args,dict):raise ValueError('candidate_tool_arguments_object_required')
        call=types.FunctionCall(id=c['id'],name=f['name'],args=args);normalized.append(call);parts.append(types.Part(function_call=call))
    if not parts:raise ValueError('empty_assistant_decision')
    return types.Content(role='model',parts=parts),normalized,text

class GroqReasoning:
    supported_reasoning_efforts=frozenset({'low','medium'})
    default_reasoning_effort='low'
    def __init__(self,events,*,transport=None,reasoning_effort=None):
        reasoning_effort=self.default_reasoning_effort if reasoning_effort is None else reasoning_effort
        if reasoning_effort not in self.supported_reasoning_efforts:raise ValueError('unsupported_groq_reasoning_effort')
        self.reasoning_effort=reasoning_effort
        from me3_assistant.backends import groq_key
        self.events=events;self.lock=asyncio.Lock();self.transport=transport or Transport(lambda event,**kw:events.emit(event,generation=getattr(events,'current_generation',None),**kw),key=groq_key(),opt_in=True)
    async def decide(self,history,declarations,continuation):
        async with asyncio.timeout(3):await self.lock.acquire()
        try:
            payload={'model':TEXT_MODEL,'messages':messages(history,continuation),'max_completion_tokens':1024,'reasoning_effort':self.reasoning_effort,'temperature':0,'stream':False}
            if declarations:payload.update(tools=[{'type':'function','function':{'name':d['name'],'description':d['description'],'parameters':d['parameters']}} for d in declarations],tool_choice='auto',parallel_tool_calls=False)
            self.events.emit('groq_reasoning_policy',generation=getattr(self.events,'current_generation',None),model=TEXT_MODEL,reasoning_effort=self.reasoning_effort,automatic_tool_execution=False)
            data=await self.transport.request('reasoning',json=payload)
            try:return decision(data)
            except ValueError as exc:
                allowed={'invalid_candidate_decision','invalid_candidate_tool_call','candidate_tool_arguments_object_required','empty_assistant_decision'}
                code=str(exc) if str(exc) in allowed else 'invalid_tool_json'
                self.events.emit('groq_decision_rejected',generation=getattr(self.events,'current_generation',None),validation_code=code,argument_values_retained=False,automatic_retry=False)
                raise
        finally:self.lock.release()
    async def close(self):await self.transport.close()
