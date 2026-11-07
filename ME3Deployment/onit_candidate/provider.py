"""Candidate reasoning quota diagnostics and read-only startup admission.

Observed headers retain authority. Error prose is transient untrusted data,
never logged, never used to refill counters, and never triggers a retry.
"""
import asyncio,json,re,time,math
from types import SimpleNamespace
from pathlib import Path
import aiohttp
from me3_assistant.groq_backend import Transport as BaseTransport,connection_trace,GROQ_CONNECTION_FAMILY
from me3_assistant.admission import Admission,blank,duration

def rejection_metadata(raw):
    result=dict(error_code='unknown',limit_scope='unknown',body_kind='unavailable',body_retained=False)
    if not isinstance(raw,bytes):return result
    if len(raw)>65536:return {**result,'body_kind':'oversized'}
    try:
        data=json.loads(raw);error=data.get('error');assert isinstance(error,dict)
        result['body_kind']='json'
        code=error.get('code')
        if code in {'rate_limit_exceeded','insufficient_quota','billing_hard_limit_reached','quota_exceeded'}:result['error_code']=code
        message=error.get('message','')
        if isinstance(message,str):
            names=[]
            for acronym,phrase,label in [('ITPM','input tokens per minute','input_tokens_per_minute'),('OTPM','output tokens per minute','output_tokens_per_minute'),('TPD','tokens per day','tokens_per_day'),('RPD','requests per day','requests_per_day'),('RPM','requests per minute','requests_per_minute')]:
                if re.search(r'\b'+acronym+r'\b|\b'+phrase+r'\b',message,re.I):names.append(label)
            if re.search(r'\bTPM\b|\btokens per minute\b',message,re.I) and not any(n in names for n in ('input_tokens_per_minute','output_tokens_per_minute')):names.append('tokens_per_minute')
            result['limit_scope']=names[0] if len(names)==1 else 'multiple_named_limits' if names else 'unknown'
            for name in ('limit','used','requested'):
                match=re.search(r'\b'+name+r'\s*[:=]?\s*([0-9]{1,10})\b',message,re.I)
                if match and int(match[1])<=1_000_000_000:result[name]=int(match[1])
    except (ValueError,TypeError,AssertionError,AttributeError,RecursionError):result['body_kind']='unclassified'
    return result

def capacity_trace(emit):
    trace=aiohttp.TraceConfig()
    async def ended(session,context,params):
        response=params.response
        if response.status!=429:return
        raw=None
        try:
            async with asyncio.timeout(1):
                chunks=[];size=0
                while size<=65536:
                    part=await response.content.read(min(16384,65537-size))
                    if not part:break
                    chunks.append(part);size+=len(part)
                raw=b''.join(chunks)
        except asyncio.CancelledError:raise
        except Exception:pass
        emit('onit_provider_capacity_rejection',role='reasoning',status=429,**rejection_metadata(raw),retry_after_seconds=duration(response.headers.get('Retry-After')),automatic_retry=False,remaining_header_values_are_not_complete_account_capacity=True)
    trace.on_request_end.append(ended);return trace

def groq_connector():
    from task_mcp.client import ROOT
    config=json.loads((ROOT/'config/core_agent.json').read_bytes());dns=config['dns_cache_seconds']
    if type(dns) is not int or not 10<=dns<=300:raise ValueError('invalid_connection_cache_bounds')
    return aiohttp.TCPConnector(family=GROQ_CONNECTION_FAMILY,limit=1,force_close=True,keepalive_timeout=None,ttl_dns_cache=dns)

class Transport(BaseTransport):
    def __init__(self,emit,*,output_quota=None,**kwargs):
        super().__init__(emit,**kwargs)
        from onit_candidate.output_quota import OutputQuota,CombinedAdmission
        from task_mcp.client import ROOT
        quota=output_quota or (OutputQuota.project(ROOT) if self.owned and kwargs.get('admission') is None else OutputQuota())
        self.admission=CombinedAdmission(self.admission,quota,emit)
        def observed(event,**fields):
            self.admission.observe_event(event,fields);emit(event,**fields)
        self.emit=observed
    async def request(self,role,**kwargs):
        if role!='reasoning':raise ValueError('candidate_reasoning_transport_required')
        if self.active:raise ValueError('candidate_request_already_active')
        payload=kwargs.get('json',{});requested=payload.get('max_completion_tokens',2048)
        cap=self.admission.output.cap(requested);self.admission.cap=cap
        kwargs={**kwargs,'json':{**payload,'max_completion_tokens':cap}}
        self.emit('onit_output_request_budget',role=role,requested_ceiling=requested,output_cap=cap,observed_output_limit=self.admission.output.limit,provider_capacity_verified=False)
        return await super().request(role,**kwargs)
    def ensure_session(self,role):
        if role!='reasoning':raise ValueError('candidate_reasoning_transport_required')
        if self.session is None:
            from me3_assistant.transport import bind_connection_deadline,REASONING_CONNECT_SECONDS
            self.emit('groq_connection_policy',address_family='IPv4',force_close=True,reuse_session_and_DNS=True,reuse_idle_socket=False,system_network_settings_changed=False,pinned_IP=False,automatic_retry=False)
            self.session=aiohttp.ClientSession(connector=groq_connector(),timeout=aiohttp.ClientTimeout(total=30,connect=REASONING_CONNECT_SECONDS),trust_env=True,trace_configs=[connection_trace(self.emit),capacity_trace(self.emit)])
            # The inherited default gives each resolved address only1s,
            # prematurely exhausting both while most of the10s creation
            # budget is unused. Match the proven candidate ASR address window;
            # DNS/TCP/TLS still share10s and the entire request still has30s.
            bind_connection_deadline(SimpleNamespace(owned_session=self.session,emit=self.emit),REASONING_CONNECT_SECONDS,address_seconds=min(4.0,REASONING_CONNECT_SECONDS/2))
        if self.session.closed:raise ValueError('groq_transport_closed')
        return self.session

def capacity_snapshot(admission,roles):
    with admission.transaction(readonly=True) as (state,now):
        reasons=[]
        for role in roles:
            row=state['roles'].get(role,blank());reason=None;until=None
            if row['blocked_until']>now:reason='provider_cooldown';until=row['blocked_until']
            elif row['lease'] and row['lease']['expires']>now:reason='model_request_active';until=row['lease']['expires']
            elif row['request_limit']==0 or row['remaining_requests']==0 and row['request_reset']>now:reason='known_request_balance';until=row['request_reset']
            elif role=='reasoning' and (row['token_limit']==0 or row['remaining_tokens']==0 and row['token_reset']>now):reason='known_token_balance';until=row['token_reset']
            if reason:reasons.append(dict(role=role,reason=reason,wait_seconds=max(0,round(until-now,3)) if until else None))
        return dict(ready=not reasons,reasons=reasons,provider_capacity_verified=False,network_calls=0,counters_changed=False)

def require_capacity(root,mode):
    result=capacity_snapshot(Admission(Path(root)/'evidence/assistant/provider-admission/groq.json'),['reasoning','asr'] if mode=='normal' else ['reasoning'])
    from onit_candidate.output_quota import OutputQuota
    output=OutputQuota.project(root);delay=output.delay(output.cap(2048))
    if delay:
        result['ready']=False;result['reasons'].append(dict(role='reasoning',reason='known_output_balance',wait_seconds=round(delay,3)))
    if not result['ready']:
        role=result['reasons'][0]['role']
        waits=[r['wait_seconds'] for r in result['reasons'] if type(r.get('wait_seconds')) in (int,float) and math.isfinite(r['wait_seconds']) and r['wait_seconds']>0]
        wait=(' Wait at least '+str(math.ceil(max(waits)))+' seconds before checking again.' if waits else ' Wait before checking again.')
        raise ValueError(('Reasoning' if role=='reasoning' else 'Speech recognition')+' service capacity is unavailable.'+wait+' Capacity after that is unverified; no automatic retry.')
    return result
