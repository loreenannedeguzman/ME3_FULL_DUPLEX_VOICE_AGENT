"""Bounded IPC and provider-envelope validation, independent of an SDK."""
import json,re
MODEL='qwen/qwen3.8-27b'
COMMIT='e0bbe9179b39ca3e928bf2d83ae972d3df6bb6c5'
MAX_FRAME=524288
MAX_NATIVE_TOOLS=80
MAX_RESPONSE_CHARS=40000
MAX_FINAL_CONTINUATIONS=8
MAX_FINAL_DRAFTS=1+MAX_FINAL_CONTINUATIONS
class CandidateError(ValueError):pass
def unique(pairs):
 result={}
 for k,v in pairs:
  if k in result:raise CandidateError('duplicate_json_key')
  result[k]=v
 return result
def bad_number(value):raise CandidateError('nonfinite_number')
def decode(raw):
 if not isinstance(raw,(bytes,str)) or len(raw)>MAX_FRAME:raise CandidateError('ipc_frame_size')
 try:return json.loads(raw,object_pairs_hook=unique,parse_constant=bad_number)
 except (ValueError,TypeError,RecursionError) as exc:raise CandidateError('invalid_ipc_json') from exc
def encode(value):
 raw=json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False,default=lambda v:v.model_dump(exclude_none=True)).encode('utf-8')+b'\n'
 if len(raw)>MAX_FRAME:raise CandidateError('ipc_frame_size')
 return raw
def tool_batch(calls):
 if not isinstance(calls,list) or not 1<=len(calls)<=3:raise CandidateError('invalid_native_batch')
 batch=[];ids=set()
 for row in calls:
  if not isinstance(row,dict) or row.get('type')!='function':raise CandidateError('invalid_native_call')
  identity=row.get('id');fn=row.get('function')
  if not isinstance(identity,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',identity) or identity in ids:raise CandidateError('invalid_native_identity')
  if not isinstance(fn,dict) or not isinstance(fn.get('name'),str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}',fn['name']):raise CandidateError('invalid_native_name')
  args=fn.get('arguments')
  # This is a bounded parsing envelope, not authority to dispatch a file write.
  # The host requires the exact discovered workspace/grant/schema contract.
  maximum=400000 if fn['name']=='workspace__write_workspace_file' else 12000
  if not isinstance(args,str) or len(args)>maximum:raise CandidateError('invalid_native_arguments')
  value=decode(args)
  if not isinstance(value,dict):raise CandidateError('invalid_native_arguments')
  ids.add(identity);batch.append(dict(id=identity,name=fn['name'],args=value))
 return batch
def validate_response(data,*,allow_plaintext_length=False,expected_model=MODEL):
 # Deliberately validate semantics we use, not vendor-specific optional metadata
 # such as Groq service_tier=on_demand. The SDK's own constructor is tolerant.
 if not isinstance(data,dict) or data.get('model')!=expected_model:raise CandidateError('unexpected_response_model')
 choices=data.get('choices')
 if not isinstance(choices,list) or len(choices)!=1 or not isinstance(choices[0],dict):raise CandidateError('invalid_response_choices')
 choice=choices[0];msg=choice.get('message');finish=choice.get('finish_reason')
 if not isinstance(msg,dict) or msg.get('role')!='assistant' or msg.get('refusal') or msg.get('function_call'):raise CandidateError('invalid_response_message')
 content=msg.get('content');calls=msg.get('tool_calls')
 if content is not None and (not isinstance(content,str) or len(content)>MAX_RESPONSE_CHARS):raise CandidateError('invalid_response_content')
 partial=(allow_plaintext_length and finish=='length' and not calls and isinstance(content,str) and bool(content.strip()) and isinstance(data.get('usage'),dict) and type(data['usage'].get('completion_tokens')) is int and data['usage']['completion_tokens']>0)
 if finish not in {'stop','tool_calls'} and not partial:raise CandidateError('incomplete_response')
 if calls:
  if finish!='tool_calls':raise CandidateError('inconsistent_native_finish')
  tool_batch(calls)
 elif finish=='tool_calls':raise CandidateError('missing_native_calls')
 elif not isinstance(content,str) or not content.strip():raise CandidateError('empty_response')
 return data
def native_payload(raw,*,expected_model=MODEL):
 if not isinstance(raw,dict) or raw.get('model')!=expected_model or raw.get('stream',False) is not False:raise CandidateError('unapproved_model_or_stream')
 messages=raw.get('messages')
 if not isinstance(messages,list) or not messages or len(messages)>128:raise CandidateError('invalid_messages')
 for m in messages:
  if not isinstance(m,dict) or m.get('role') not in {'system','user','assistant','tool'}:raise CandidateError('invalid_message')
  if not isinstance(m.get('content'),(str,type(None))):raise CandidateError('unsupported_message_content')
 output=raw.get('max_completion_tokens',raw.get('max_tokens',2048))
 if type(output) is not int or output<1:raise CandidateError('invalid_output_budget')
 result=dict(model=expected_model,messages=messages,max_completion_tokens=min(output,2048),temperature=0,reasoning_effort='none',include_reasoning=False,stream=False)
 if raw.get('tools'):
  if not isinstance(raw['tools'],list) or len(raw['tools'])>MAX_NATIVE_TOOLS:raise CandidateError('invalid_tool_payload')
  result.update(tools=raw['tools'],tool_choice=raw.get('tool_choice','auto'),parallel_tool_calls=False)
 if raw.get('response_format') is not None:result['response_format']=raw['response_format']
 return result
