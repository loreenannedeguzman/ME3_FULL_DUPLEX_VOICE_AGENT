from onit_candidate.reasoning_profile import GEMINI_MODEL,GROQ_MODEL,OPENAI_MODEL,OPENAI_PROVIDER
"""Original full-Onit task loop in a networkless process; JSON IPC only."""
import asyncio,importlib,json,sys,types
from openai._models import construct_type
from openai.types.chat import ChatCompletion
from onit_candidate.native_harness import NAMES as NATIVE_NAMES,upstream_declarations,upstream_name,native_result
from onit_candidate.protocol import MODEL,decode,encode,validate_response,tool_batch,MAX_FINAL_CONTINUATIONS,MAX_FINAL_DRAFTS,MAX_RESPONSE_CHARS
from onit_candidate.answer_guard import clean_answer
from onit_candidate.protocol import CandidateError
c=importlib.import_module('src.model.serving.chat')
class StopCandidate(BaseException):
 def __init__(self,code,diagnostics=None):super().__init__(code);self.diagnostics=diagnostics

def validate_child_model(model,provider,*,floor_mode=False,qualification_role=None,declarations=None):
 if qualification_role is not None:
  if qualification_role!='qwen_floor268' or floor_mode is not True or declarations!=[] or provider!='groq_qwen_floor_qualification' or model!='qwen/qwen3.8-27b':raise StopCandidate('explicit_tool_free_floor_qualification_only')
  return
 if provider==OPENAI_PROVIDER:
  if model!=OPENAI_MODEL:raise StopCandidate('unapproved_reasoning_model')
 elif provider not in {'legacy','google','groq'} or model not in {MODEL,GEMINI_MODEL}:
  raise StopCandidate('unapproved_reasoning_model')

class OutputVisibility:
 """Counts only. Actual provider usage and opaque reasoning remain unchanged."""
 def __init__(self,original,expected_model=OPENAI_MODEL):self.original=original;self.last=None;self.expected_model=expected_model
 def clear(self):self.last=None
 def observe(self,result):
  self.clear();usage=result.get('usage');details=usage.get('completion_tokens_details',{}) if isinstance(usage,dict) else None
  total=usage.get('completion_tokens') if isinstance(usage,dict) else None
  reasoning=details.get('reasoning_tokens',0) if isinstance(details,dict) else None
  if result.get('model')!=self.expected_model or type(total) is not int or not 0<=total<=10000000 or type(reasoning) is not int or not 0<=reasoning<=total:raise StopCandidate('invalid_reasoning_usage_subset')
  self.last=(total,reasoning)
 def check(self,total,*delivered):
  if self.last is None or type(total) is not int or total!=self.last[0]:raise StopCandidate('current_validated_usage_required')
  return self.original(total-self.last[1],*delivered)

async def broker_chat(provider,visibility,**kwargs):
 if provider not in {OPENAI_PROVIDER,'groq_qwen_floor_qualification'}:return await c.chat(**kwargs)
 if kwargs.get('host')!='https://api.openai.com/v1' or kwargs.get('host_key')!='BROKER_OWNS_CREDENTIALS' or kwargs.get('model')!=(GROQ_MODEL if provider=='groq_qwen_floor_qualification' else OPENAI_MODEL) or not isinstance(visibility,OutputVisibility):raise StopCandidate('exact_local_IPC_broker_entry_required')
 # This child has a ChatCompletion-shaped JSON IPC client and no network.
 # Parent me3_openai alone owns real Responses HTTP and account credentials.
 kwargs=dict(kwargs,host='https://me3-broker.invalid/v1');original=c._output_unaccounted;c._output_unaccounted=visibility.check
 try:return await c.chat(**kwargs)
 finally:c._output_unaccounted=original
class Pipe:
 def __init__(self,reader):self.reader=reader;self.sequence=0;self.delivery=None
 async def rpc(self,kind,**payload):
  self.sequence+=1;sequence=self.sequence
  sys.stdout.buffer.write(encode(dict(v=1,sequence=sequence,kind=kind,**payload)));sys.stdout.buffer.flush()
  while True:
   raw=await self.reader.readline()
   if not raw:raise StopCandidate('broker_closed')
   row=decode(raw)
   if row.get('v')!=1 or row.get('sequence')!=sequence:raise StopCandidate('invalid_broker_reply')
   if 'interim' in row:
    if set(row)!={'v','sequence','interim'} or kind!='model' or self.delivery is None:raise StopCandidate('unbound_delivery_interim')
    ack=self.delivery.observe(sequence,row['interim'])
    sys.stdout.buffer.write(encode(dict(v=1,sequence=sequence,kind='delivery_ack',ack=ack)));sys.stdout.buffer.flush()
    continue
   if 'result' not in row:raise StopCandidate('invalid_broker_reply')
   if kind=='model' and self.delivery is not None:self.delivery.terminal(row['result'])
   return row['result']
class Registry:
 def __init__(self,pipe,declarations):self.pipe=pipe;self.ds={d['name']:d for d in declarations};self.tools=self.ds;self.closing=False;self.harness=None
 def get_tool_items(self):return [{'type':'function','function':d} for d in self.ds.values()]
 def tool_accepts_param(self,name,key):return False # Private identities belong to the host gateway.
 def parameters_schema(self,name):return self.ds[name]['parameters']
 def __getitem__(self,name):
  async def call(log_handler=None,**args):
   raw=await self.pipe.rpc('call',name=name,args=args)
   if isinstance(raw,dict) and set(raw)=={'_me3_tool_result','_me3_declarations'}:
    ds=raw['_me3_declarations']
    if not isinstance(raw['_me3_tool_result'],str) or not isinstance(ds,list):raise StopCandidate('invalid_host_schema_refresh')
    self.ds={d['name']:d for d in ds};raw=raw['_me3_tool_result']
   if name in NATIVE_NAMES:
    if not self.harness or json.loads(raw)!={'execute_native':True}:raise StopCandidate('native_context_dispatch_not_bound')
    raw=native_result(name,await self.harness.adispatch(upstream_name(name),args))
    await self.pipe.rpc('native_result',name=name,args=args,text=raw)
    return raw
   if self.harness and name not in {'finish_conversation','load_operation_schemas'} and not name.startswith(('propose_','soundcloud_')):
    stored=self.harness.results.put(name,raw)
    if stored is not None:raw=stored
   if name=='finish_conversation':self.closing=True;self.ds.clear()
   if name=='load_operation_schemas':
    data=json.loads(raw);ds=data.pop('_schemas');self.ds.clear();self.ds.update({d['name']:d for d in ds});return json.dumps(data,ensure_ascii=False)
   return raw
  return call
async def run(init,pipe):
 if 'validated_delivery' in init:
  from onit_candidate.validated_delivery import ChildDelivery
  pipe.delivery=ChildDelivery(init['validated_delivery'])
 model=init.get('model',MODEL)
 provider=init.get('reasoning_provider','legacy')
 validate_child_model(model,provider,floor_mode=init.get('floor_admission',False),qualification_role=init.get('floor_qualification_role'),declarations=init.get('declarations'))
 visibility=OutputVisibility(c._output_unaccounted,model) if provider in {OPENAI_PROVIDER,'groq_qwen_floor_qualification'} else None
 floor_mode=init.get('floor_admission',False)
 if type(floor_mode) is not bool or floor_mode and init['declarations']:raise StopCandidate('tool_free_floor_scope_required')
 if 'speech_languages' in init:
  from onit_candidate.speech_presentation import languages,SUPPORTED
  try:
   if floor_mode or languages(init['speech_languages'])!=SUPPORTED:raise CandidateError('invalid_host_speech_languages')
  except CandidateError:raise StopCandidate('invalid_final_speech_presentation')
 registry=Registry(pipe,init['declarations']);final=dict(drafts=0,text='',complete=False)
 if not floor_mode:
  from src.model.serving.harness import HarnessTools
  from pathlib import Path
  Path('/work/native-context').mkdir(exist_ok=False)
  registry.harness=HarnessTools(data_path='/work/native-context',enabled=True,result_store=True,code_execution=False)
  if [x['function'] for x in registry.harness.tool_items()]!=upstream_declarations():raise StopCandidate('pinned_native_context_schema_changed')
 class Client:
  def __init__(self,**kw):self.chat=types.SimpleNamespace(completions=self)
  async def create(self,**kw):
   if floor_mode and final['drafts']:raise StopCandidate('floor_single_request_limit')
   if pipe.delivery is not None and pipe.delivery.sealed:raise StopCandidate('delivery_model_after_seal')
   if final['complete']:raise StopCandidate('native_final_already_complete')
   if final['drafts'] or registry.closing:kw.pop('tools',None);kw.pop('tool_choice',None)
   else:kw['tools']=registry.get_tool_items() # Current host-refreshed schemas in both eager and lazy modes.
   if visibility:visibility.clear()
   result=validate_response(await pipe.rpc('model',payload=kw),allow_plaintext_length=True,expected_model=model)
   if visibility:visibility.observe(result)
   if registry.harness is not None:registry.harness.observe(prompt_tokens=result.get('usage',{}).get('prompt_tokens',0),max_context_tokens=32768,turns=registry.harness.turns+1)
   choice=result['choices'][0];message=choice['message'];calls=message.get('tool_calls')
   if calls:
    if final['drafts']:raise StopCandidate('native_final_tool_after_text')
   else:
    piece=c._extract_final_response(message['content'],'','');joined=c._stitch_continuation(final['text'],piece)
    if final['drafts'] and len(joined)<=len(final['text']):raise StopCandidate('native_final_stalled')
    clean_answer(joined)
    if len(joined)>MAX_RESPONSE_CHARS or final['drafts']>=MAX_FINAL_DRAFTS:raise StopCandidate('native_final_bound')
    final['drafts']+=1;final['text']=joined;registry.ds.clear()
    used=result.get('usage',{}).get('completion_tokens',0)
    incomplete=c._looks_incomplete(joined);unaccounted=c._output_unaccounted(used,message['content'],'')
    final['complete']=choice['finish_reason']=='stop' and not incomplete and not unaccounted
    if choice['finish_reason']=='stop' and not final['complete']:
     raise StopCandidate('native_final_not_complete',dict(incomplete_markup=bool(incomplete),output_unaccounted=bool(unaccounted),joined_characters=len(joined),drafts=final['drafts']))
   # Match the SDK response parsing path, with strict validation above for the
   # fields that control execution. Optional vendor metadata stays tolerant.
   return construct_type(type_=ChatCompletion,value=result)
  async def close(self):pass
 c.AsyncOpenAI=Client
 original=c._handle_structured_tool_calls
 async def guarded_batch(calls,*args,**kwargs):
  if pipe.delivery is not None and pipe.delivery.sealed:raise StopCandidate('delivery_tools_after_seal')
  native=[dict(id=t.id,type='function',function=dict(name=t.function.name,arguments=t.function.arguments)) for t in calls]
  bound=await pipe.rpc('batch',calls=tool_batch(native))
  if isinstance(bound,dict) and bound.get('validated') is True and isinstance(bound.get('_schemas'),list):
   registry.ds.clear();registry.ds.update({d['name']:d for d in bound['_schemas']})
  return await original(calls,*args,**kwargs)
 c._handle_structured_tool_calls=guarded_batch
 metrics={};run_state=c.RunState()
 answer=await broker_chat(provider,visibility,host='https://api.openai.com/v1',host_key='BROKER_OWNS_CREDENTIALS',model=model,instruction=init['request'],tool_registry=registry,timeout=30,stream=False,think=False,safety_queue=asyncio.Queue(),prompt_intro=init['policy'],session_history=init.get('history'),temperature=0,max_tokens=2048,max_context_tokens=32768,max_chat_iterations=init.get('tool_rounds',6)+MAX_FINAL_CONTINUATIONS,max_api_retries=1,max_planning_continuations=0,max_ack_continuations=0,max_final_continuations=MAX_FINAL_CONTINUATIONS,max_final_stalls=0,max_repetition_continuations=0,max_repeat_recoveries=0,max_nudge_decline_recoveries=0,verify_answers=False,verify_background=False,harness_tools=False,result_store=False,code_execution=False,metrics=metrics,run_state=run_state)
 # Native c.chat has explicit non-answer exits (turn limits, repetition,
 # safety abort). Never accept a trimmed failure just because HTTP said stop.
 if run_state.stop_reason!=c.STOP_ANSWERED:raise StopCandidate('native_stop_not_answered',dict(stop_reason=run_state.stop_reason if run_state.stop_reason in {'answered','turn_limit','repeated_tool_call','planning_exhausted','repetition_loop','safety_abort'} else 'unrecognized',drafts=final['drafts'],final_complete=final['complete']))
 if not final['complete']:raise StopCandidate('native_final_not_complete')
 expected=c._recover_dropped_answer(final['text'],run_state.prose_before_tools)
 if answer!=expected:raise StopCandidate('native_final_mismatch')
 if not isinstance(answer,str) or not answer.strip() or len(answer)>MAX_RESPONSE_CHARS or c._looks_incomplete(answer):raise StopCandidate('native_final_not_complete')
 clean_answer(answer)
 relationship='unchanged' if answer==final['text'] else 'recovered_pretool_prose' 
 # Answer fact-checking is off for this initial transport/read-only stage;
 # independent tool verification is never delegated to an LLM. Later enabled
 # model stages must use the same counted broker, not background defaults.
 if 'speech_languages' in init:
  from onit_candidate.speech_presentation import parse_final
  try:
   if pipe.delivery is not None:
    from onit_candidate.validated_delivery import parse_units
    parse_units(answer)
   else:parse_final(answer,init['speech_languages'])
  except CandidateError:raise StopCandidate('invalid_final_speech_presentation')
 await pipe.rpc('done',answer=answer,metrics=metrics,finalization=dict(drafts=final['drafts'],complete=True,native_answered=True,relationship=relationship))
async def main():
 reader=asyncio.StreamReader(limit=524289);protocol=asyncio.StreamReaderProtocol(reader);await asyncio.get_running_loop().connect_read_pipe(lambda:protocol,sys.stdin.buffer)
 init=decode(await reader.readline());pipe=Pipe(reader)
 try:await run(init,pipe)
 except StopCandidate as exc:await pipe.rpc('failed',code=str(exc),diagnostics=exc.diagnostics)
 except CandidateError as exc:
  if str(exc)!='native_tool_markup_in_answer':raise
  await pipe.rpc('failed',code='native_tool_markup_in_answer',diagnostics=None)
if __name__=='__main__':asyncio.run(main())
