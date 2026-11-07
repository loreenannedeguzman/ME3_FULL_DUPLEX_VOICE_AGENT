"""Explicit native Gemini wire adapter; same counted transport, no fallback or executor."""
import copy,json,uuid
from onit_candidate.gemini_provider import Transport as CountedTransport
from onit_candidate.protocol import CandidateError,tool_batch
from onit_candidate.reasoning_profile import GEMINI_MODEL
ENDPOINT='https://generativelanguage.googleapis.com/v1beta/models/'+GEMINI_MODEL+':generateContent'

def encode_request(payload):
    if payload.get('model')!=GEMINI_MODEL:raise CandidateError('native_wire_model_not_selected')
    instructions=[];contents=[];calls={}
    for index,message in enumerate(payload['messages']):
        role=message['role'];content=message.get('content');parts=[]
        if role=='system':
            if content:instructions.append(dict(text=content))
            continue
        if role=='tool':
            identity=message.get('tool_call_id')
            if identity not in calls:raise CandidateError('native_wire_unmatched_function_response')
            name,native_id=calls[identity];response=dict(name=name,response=dict(result=content))
            if native_id is not None:response['id']=native_id
            parts=[dict(functionResponse=response)];wire_role='user'
        else:
            wire_role='model' if role=='assistant' else 'user'
            if content:parts.append(dict(text=content))
            native_calls=message.get('tool_calls') or []
            if native_calls:
                if role!='assistant':raise CandidateError('native_wire_call_role')
                batch=tool_batch(native_calls)
                for original,call in zip(native_calls,batch):
                    metadata=original.get('extra_content',{}).get('google',{});native_id=metadata.get('native_call_id')
                    if native_id is not None and (not isinstance(native_id,str) or len(native_id)>256):raise CandidateError('native_wire_call_identity_bound')
                    calls[call['id']]=(call['name'],native_id);function=dict(name=call['name'],args=call['args'])
                    if native_id is not None:function['id']=native_id
                    part=dict(functionCall=function);signature=metadata.get('thought_signature')
                    if signature is not None:
                        if not isinstance(signature,str) or len(signature)>131072:raise CandidateError('native_wire_signature_bound')
                        part['thoughtSignature']=signature
                    parts.append(part)
        if not parts:
            # Onit's session history pairs include an empty assistant response
            # for a cancelled/no-reply turn. It has no wire content: retain the
            # user's earlier request and structured host delivery metadata,
            # without inventing a reply or dropping any tools/effect evidence.
            if role=='assistant' and content in (None,'') and not native_calls and index<len(payload['messages'])-1:continue
            raise CandidateError('native_wire_empty_message')
        if role=='tool' and contents and contents[-1]['role']=='user' and all('functionResponse' in p for p in contents[-1]['parts']):
            contents[-1]['parts'].extend(parts)
        else:contents.append(dict(role=wire_role,parts=parts))
    if not contents or contents[-1]['role']!='user':raise CandidateError('native_wire_current_user_boundary')
    result=dict(contents=contents,generationConfig=dict(maxOutputTokens=payload['max_tokens'],thinkingConfig=dict(thinkingLevel='minimal')))
    if instructions:result['systemInstruction']=dict(parts=instructions)
    if payload.get('tools'):
        declarations=[]
        for tool in payload['tools']:
            if tool.get('type')!='function':raise CandidateError('native_wire_tool_type')
            f=tool['function'];declarations.append(dict(name=f['name'],description=f['description'],parametersJsonSchema=copy.deepcopy(f['parameters'])))
        result['tools']=[dict(functionDeclarations=declarations)];choice=payload.get('tool_choice','auto')
        if choice!='auto':raise CandidateError('native_wire_tool_choice_not_supported')
        result['toolConfig']=dict(functionCallingConfig=dict(mode='AUTO'))
    if payload.get('response_format') is not None:raise CandidateError('native_wire_response_format_not_supported')
    if len(json.dumps(result,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode())>524288:raise CandidateError('native_wire_request_bound')
    return result

def decode_response(data):
    if not isinstance(data,dict):raise CandidateError('native_wire_response_object')
    version=data.get('modelVersion')
    if version is not None and version!=GEMINI_MODEL and (not isinstance(version,str) or not version.startswith(GEMINI_MODEL+'-')):raise CandidateError('native_wire_model_mismatch')
    candidates=data.get('candidates')
    if not isinstance(candidates,list) or len(candidates)!=1:raise CandidateError('native_wire_candidate_count')
    candidate=candidates[0];finish=candidate.get('finishReason');content=candidate.get('content',{})
    if finish not in {'STOP','MAX_TOKENS'} or content.get('role')!='model':raise CandidateError('native_wire_incomplete_or_blocked')
    parts=content.get('parts')
    if not isinstance(parts,list) or not 1<=len(parts)<=256:raise CandidateError('native_wire_parts_bound')
    text=[];calls=[]
    for part in parts:
        if not isinstance(part,dict):raise CandidateError('native_wire_invalid_part')
        if part.get('thought') is True:continue # Never expose hidden reasoning as an answer.
        if 'text' in part:
            if not isinstance(part['text'],str):raise CandidateError('native_wire_text_type')
            text.append(part['text'])
        elif 'functionCall' in part:
            f=part['functionCall']
            if not isinstance(f,dict) or not isinstance(f.get('args',{}),dict):raise CandidateError('native_wire_function_arguments')
            call=dict(id='native_'+uuid.uuid4().hex,type='function',function=dict(name=f.get('name'),arguments=json.dumps(f.get('args',{}),ensure_ascii=False,allow_nan=False)))
            metadata={}
            if 'thoughtSignature' in part:
                signature=part['thoughtSignature']
                if not isinstance(signature,str) or len(signature)>131072:raise CandidateError('native_wire_signature_bound')
                metadata['thought_signature']=signature
            if 'id' in f:
                if not isinstance(f['id'],str) or len(f['id'])>256:raise CandidateError('native_wire_call_identity_bound')
                metadata['native_call_id']=f['id']
            if metadata:call['extra_content']=dict(google=metadata)
            calls.append(call)
        else:raise CandidateError('native_wire_unsupported_part')
    if calls:
        tool_batch(calls)
        if finish!='STOP':raise CandidateError('native_wire_partial_function_call')
    elif not ''.join(text).strip():raise CandidateError('native_wire_empty_answer')
    usage=data.get('usageMetadata',{});safe={}
    for old,new in [('promptTokenCount','prompt_tokens'),('totalTokenCount','total_tokens')]:
        value=usage.get(old)
        if type(value) is not int or not 0<=value<=1000000:raise CandidateError('native_wire_usage_missing')
        safe[new]=value
    visible=usage.get('candidatesTokenCount');thinking=usage.get('thoughtsTokenCount',0)
    if type(visible) is not int or type(thinking) is not int or not 0<=visible<=1000000 or not 0<=thinking<=1000000:raise CandidateError('native_wire_usage_missing')
    safe['completion_tokens']=visible+thinking;safe['completion_tokens_details']=dict(reasoning_tokens=thinking)
    return dict(model=GEMINI_MODEL,choices=[dict(index=0,message=dict(role='assistant',content=''.join(text) or None,**(dict(tool_calls=calls) if calls else {})),finish_reason='tool_calls' if calls else 'length' if finish=='MAX_TOKENS' else 'stop')],usage=safe)

class Transport(CountedTransport):
    def wire_request(self,payload):return ENDPOINT,encode_request(payload),{'x-goog-api-key':self.key}
    def decode_response(self,data):return decode_response(data)
