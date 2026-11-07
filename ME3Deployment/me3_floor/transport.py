"""One inactive Qwen role, preserving existing generic Groq and output books."""
import copy,hashlib,importlib.util,json
from pathlib import Path
from onit_candidate.provider import Transport as ExistingGroqTransport
from me3_assistant.admission import estimated_tokens
P=Path(__file__).resolve().parent
s=importlib.util.spec_from_file_location('role_ledger268',P/'role_ledger.py');role=importlib.util.module_from_spec(s);s.loader.exec_module(role)
RoleLedger=role.RoleLedger;MODEL=role.MODEL;ROLE=role.ROLE;CAP=role.CAP;LEDGER_NAME=role.LEDGER_NAME
PROVIDER='groq_qwen_floor_qualification'
def need(v,c):
    if not v:raise ValueError(c)
def digest(v):return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
def payload(raw):
    allowed={'model','messages','max_completion_tokens','temperature','reasoning_effort','include_reasoning','stream','response_format'}
    need(type(raw) is dict and set(raw)<=allowed and raw.get('model')==MODEL,'exact_tool_free_Qwen_required')
    need(raw.get('reasoning_effort','none')=='none' and raw.get('include_reasoning',False) is False and raw.get('stream',False) is False and raw.get('temperature',0)==0,'fixed_none_nonstream_policy_required')
    need(raw.get('response_format',{'type':'json_object'})=={'type':'json_object'},'JSON_object_required')
    messages=raw.get('messages');need(type(messages) is list and 1<=len(messages)<=16,'bounded_text_history_required')
    need(all(type(m) is dict and set(m)=={'role','content'} and m['role'] in {'system','user','assistant'} and type(m['content']) is str for m in messages),'text_only_history_required')
    need(type(raw.get('max_completion_tokens',CAP)) is int and raw.get('max_completion_tokens',CAP)>0,'positive_output_bound_required')
    result=dict(model=MODEL,messages=copy.deepcopy(messages),max_completion_tokens=CAP,temperature=0,reasoning_effort='none',include_reasoning=False,stream=False,response_format={'type':'json_object'})
    need(len(json.dumps(result,ensure_ascii=False,allow_nan=False).encode())<=20000,'bounded_floor_request_required')
    return result
def response(value):
    need(type(value) is dict and value.get('model')==MODEL,'floor_response_model_mismatch')
    choices=value.get('choices');need(type(choices) is list and len(choices)==1 and type(choices[0]) is dict,'single_choice_required')
    choice=choices[0];message=choice.get('message')
    need(choice.get('finish_reason')=='stop' and type(message) is dict and message.get('role')=='assistant' and type(message.get('content')) is str and 0<len(message['content'])<=256,'complete_bounded_floor_response_required')
    need(not message.get('tool_calls') and not message.get('function_call') and not message.get('refusal') and not message.get('reasoning'),'tool_free_no_reasoning_response_required')
    usage=value.get('usage');need(type(usage) is dict and all(type(usage.get(k)) is int and usage[k]>=0 for k in ('prompt_tokens','completion_tokens','total_tokens')),'actual_usage_required')
    need(usage['prompt_tokens']+usage['completion_tokens']==usage['total_tokens']<=1000000 and 0<usage['completion_tokens']<=CAP,'actual_output128_required')
    return value
class PreparedCombined:
    """Single handoff of already-reserved CombinedAdmission ticket, never fresh quota."""
    def __init__(self,base,ticket,estimate):self.base=base;self.ticket=ticket;self.estimate=estimate;self.claimed=False;self.finished=False
    def __getattr__(self,name):return getattr(self.base,name)
    def capacity_delay(self,role,estimate,maximum):
        need(role=='reasoning' and estimate==self.estimate,'prepared_request_changed');return 0
    def admit(self,role,estimate):
        need(role=='reasoning' and estimate==self.estimate and not self.claimed and not self.finished,'provider_ticket_once_required');self.claimed=True;return self.ticket
    def observe(self,role,ticket,headers,status):
        need(role=='reasoning' and ticket==self.ticket and not self.finished,'provider_ticket_bound_required');return self.base.observe(role,ticket,headers,status)
    def finish(self,role,ticket):
        need(role=='reasoning' and ticket==self.ticket and not self.finished,'provider_ticket_once_required');self.finished=True;return self.base.finish(role,ticket)
class Transport(ExistingGroqTransport):
    provider=PROVIDER;model=MODEL;qualification_role=ROLE
    def __init__(self,emit,*,ledger,**kwargs):
        self.dispatched=False;self.actual=None
        def observed(event,**fields):
            if event=='groq_request_dispatch' and fields.get('role')=='reasoning':self.dispatched=True
            if event=='groq_reported_token_usage' and fields.get('role')=='reasoning':
                actual=fields.get('completion_tokens')
                if type(actual) is int and 0<=actual<=CAP:self.actual=actual
            emit(event,**fields)
        super().__init__(observed,**kwargs)
        self.combined=self.admission;self.ledger=ledger;self.goal=None;self.ordinal=None;self.prepared=None;self.used=False;self.terminal=False
    def begin_goal(self,goal):
        need(self.goal is None and not self.used,'floor_role_attempt_already_consumed');self.goal=goal
    def prepare_reservation(self,raw):
        need(self.goal is not None and not self.used and self.prepared is None,'floor_preflight_once_required')
        normalized=payload(raw);estimate=estimated_tokens('reasoning',dict(json=normalized))
        self.ledger.prepare(self.goal);self.used=True
        try:
            self.combined.cap=CAP;ticket=self.combined.admit('reasoning',estimate)
            self.admission=PreparedCombined(self.combined,ticket,estimate);self.prepared=digest(normalized)
        except BaseException:
            self.ledger.finish(self.goal,'FAILED',dispatched=False);self.terminal=True;raise
    def bind_reservation(self,ordinal):
        need(self.prepared is not None and self.ordinal is None,'provider_preflight_before_shared_required')
        self.ledger.bind(self.goal,ordinal);self.ordinal=ordinal
    def ensure_session(self,role):
        session=super().ensure_session(role);need(hasattr(session,'_retry_connection'),'explicit_retry_control_required');session._retry_connection=False;return session
    async def request(self,role,*,json,capacity_wait_seconds=0):
        import asyncio
        normalized=payload(json)
        need(role=='reasoning' and self.ordinal is not None and self.prepared==digest(normalized) and not self.active and not self.terminal,'prepared_shared_floor_request_required')
        state='FAILED'
        try:
            value=response(await super().request(role,json=normalized,capacity_wait_seconds=0))
            self.actual=value['usage']['completion_tokens'];state='COMPLETED';return value
        except asyncio.CancelledError:state='INTERRUPTED';raise
        finally:
            self.ledger.finish(self.goal,state,dispatched=self.dispatched,actual=self.actual);self.terminal=True
    def end_goal(self):
        need(not self.active,'settled_request_required')
        if self.prepared is not None and not self.admission.finished:self.admission.finish('reasoning',self.admission.ticket)
        if self.used and not self.terminal:self.ledger.finish(self.goal,'FAILED',dispatched=False);self.terminal=True
        self.goal=None;self.ordinal=None
