"""Provider-free, stateless Responses bridge for the manual Onit tool loop.

No authentication, requests, retries, tool execution or persistence lives here.
The byte/event limits bound the receiving host, NOT generated tokens or spend.
Opaque completed reasoning items live only in this codec instance for replay.
"""
from __future__ import annotations

import asyncio
import codecs
import copy
import inspect
import json
import re

ENDPOINT = "https://api.openai.com/v1/responses"
MAX_STREAM_BYTES = 4 * 1024 * 1024
MAX_EVENT_BYTES = 1024 * 1024
MAX_EVENTS = 8192
NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ResponsesError(ValueError):
    """Fixed public codes only; never includes payloads, credentials or URLs."""


def fail(code):
    raise ResponsesError(code)


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            fail("responses_duplicate_json_key")
        result[key] = value
    return result


def dumps(value):
    try:
        result = json.dumps(value, ensure_ascii=False, allow_nan=False,
                            separators=(",", ":"))
        result.encode("utf-8", errors="strict")
        return result
    except (ValueError, UnicodeError, TypeError, RecursionError):
        fail("responses_invalid_json")


def loads(value):
    try:
        parsed = json.loads(value, object_pairs_hook=unique,
                            parse_constant=lambda _: fail("responses_invalid_json"))
        dumps(parsed)  # Also reject overflowed floats and lone surrogates.
        return parsed
    except ResponsesError:
        raise
    except (ValueError, UnicodeError, TypeError, RecursionError):
        fail("responses_invalid_json")


def bounded_text(value, maximum=40000):
    if not isinstance(value, str) or len(value) > maximum:
        fail("responses_invalid_text")
    dumps(value)
    return value


def integer(value):
    if type(value) is not int or not 0 <= value <= 10_000_000:
        fail("responses_invalid_usage")
    return value


def _same_typed_json(value, expected):
    """Exact fixed schema comparison: bools never compare equal to integers."""
    if type(value) is not type(expected):
        return False
    if type(expected) is dict:
        return value.keys() == expected.keys() and all(
            _same_typed_json(value[key], item) for key, item in expected.items())
    if type(expected) is list:
        return len(value) == len(expected) and all(
            _same_typed_json(actual, item) for actual, item in zip(value, expected))
    return value == expected


def unit_response_format():
    """Inactive323 host schema: model supplies text/phase, never host authority."""
    return {'type':'json_schema','json_schema':{
        'name':'me3_final_units','strict':True,'schema':{
            'type':'object','properties':{
                'phase':{'type':'string','enum':['FINAL','WHOLE']},
                'units':{'type':'array','items':{
                    'type':'object','properties':{
                        'index':{'type':'integer','minimum':0,'maximum':31},
                        'language':{'type':'string','enum':['en','es']},
                        'text':{'type':'string','minLength':1,'maxLength':40000}},
                    'required':['index','language','text'],'additionalProperties':False},
                    'minItems':1,'maxItems':32},
                'complete':{'type':'boolean','enum':[True]}},
            'required':['phase','units','complete'],'additionalProperties':False}}}


def _unit(value, index, available):
    if not isinstance(value,dict) or list(value)!=['index','language','text']:
        fail('final_units_invalid_unit_keys')
    if type(value['index']) is not int or value['index']!=index or not 0<=index<32:
        fail('final_units_invalid_index')
    if type(value['language']) is not str or value['language'] not in available:
        fail('final_units_invalid_language')
    text=bounded_text(value['text'])
    if not text.strip() or '\x00' in text:fail('final_units_invalid_text')
    return copy.deepcopy(value)


def parse_final_units(raw,available=('en','es')):
    """Strict terminal projection only; does not establish native completion."""
    if not isinstance(available,(tuple,list)) or tuple(available) not in (('en',),('en','es')):fail('final_units_invalid_languages')
    value=loads(bounded_text(raw,262144))
    if not isinstance(value,dict) or list(value)!=['phase','units','complete']:
        fail('final_units_invalid_root_keys')
    if type(value['phase']) is not str or value['phase'] not in {'FINAL','WHOLE'} or value['complete'] is not True:
        fail('final_units_invalid_final_marker')
    if not isinstance(value['units'],list) or not 1<=len(value['units'])<=32:
        fail('final_units_unit_bound')
    units=[_unit(row,index,available) for index,row in enumerate(value['units'])]
    text=''.join(row['text'] for row in units)
    if len(text)>40000:fail('final_units_text_bound')
    return text,[{'language':row['language'],'text':row['text']} for row in units],units


def structured_text_format(value, *, validated_final_units=False):
    """Only the fixed host-owned speech envelope; no arbitrary wire options.

    Chat response_format maps to Responses text.format on the SAME request.
    Keep this leaf dependency-free; the Engine independently stamps its host
    schema each bilingual round, including after tool results.
    """
    expected = {"type": "json_schema", "json_schema": {
        "name": "me3_speech_presentation", "strict": True, "schema": {
            "type": "object", "properties": {"segments": {
                "type": "array", "items": {
                    "type": "object", "properties": {
                        "language": {"type": "string", "enum": ["en", "es"]},
                        "text": {"type": "string", "minLength": 1, "maxLength": 40000}},
                    "required": ["language", "text"], "additionalProperties": False},
                "minItems": 1, "maxItems": 32}},
            "required": ["segments"], "additionalProperties": False}}}
    if validated_final_units is True and _same_typed_json(value,unit_response_format()):
        return {'type':'json_schema',**copy.deepcopy(value['json_schema'])}
    if not _same_typed_json(value, expected):
        fail("responses_unsupported_response_format")
    return {"type": "json_schema", **copy.deepcopy(expected["json_schema"])}


def assistant_key(message):
    """Only the exact visible assistant/call projection can select opaque replay."""
    calls = message.get("tool_calls", [])
    normalized = []
    if not isinstance(calls, list):
        fail("responses_invalid_history")
    for call in calls:
        if not isinstance(call, dict) or call.get("type") != "function":
            fail("responses_invalid_history")
        function = call.get("function", {})
        if not isinstance(function, dict):
            fail("responses_invalid_history")
        normalized.append([call.get("id"), function.get("name"), function.get("arguments")])
    return dumps([message.get("content") or None, normalized])


class ResponsesCodec:
    def __init__(self, model, *, allow_no_server_output_cap=False, namespace="me3",validated_final_units=False):
        if not isinstance(model, str) or not model or len(model) > 128 or any(c.isspace() for c in model):
            fail("responses_model_required")
        if not isinstance(namespace, str) or not NAME.fullmatch(namespace):
            fail("responses_invalid_namespace")
        self.model = model
        self.namespace = namespace
        self.allow_no_server_output_cap = allow_no_server_output_cap is True
        self.validated_final_units=validated_final_units is True
        self._replay = {}
        self._pending = None
        self._seen_calls = set()

    def abort(self):
        """Clear an abandoned request; caller must still obey first-failure policy."""
        self._pending = None

    def encode_request(self, payload):
        if not self.allow_no_server_output_cap:
            fail("responses_no_server_output_cap_requires_acknowledgment")
        if self._pending is not None:
            fail("responses_request_already_pending")
        if not isinstance(payload, dict) or payload.get("model", self.model) != self.model:
            fail("responses_model_mismatch")
        messages = payload.get("messages")
        tools = payload.get("tools", [])
        if not isinstance(messages, list) or not 1 <= len(messages) <= 128:
            fail("responses_invalid_history")
        if not isinstance(tools, list) or len(tools) > 80:
            fail("responses_invalid_tools")
        functions = []
        names = set()
        for tool in tools:
            if not isinstance(tool, dict) or tool.get("type") != "function":
                fail("responses_unsupported_tool")
            function = tool.get("function", {})
            if not isinstance(function, dict):
                fail("responses_invalid_tools")
            name = function.get("name")
            if not isinstance(name, str) or not NAME.fullmatch(name) or name in names:
                fail("responses_invalid_tool_name")
            parameters = function.get("parameters")
            if not isinstance(parameters, dict) or parameters.get("type") != "object":
                fail("responses_invalid_tool_schema")
            names.add(name)
            functions.append({"type": "function", "name": name,
                              "description": bounded_text(function.get("description", ""), 12000),
                              "parameters": copy.deepcopy(parameters), "strict": False})
        inputs = []
        unresolved = {}
        history_calls = set()
        for message in messages:
            if not isinstance(message, dict):
                fail("responses_invalid_history")
            role = message.get("role")
            if role == "tool":
                call_id = message.get("tool_call_id")
                if not isinstance(call_id, str) or call_id not in unresolved or message.get("name", unresolved.get(call_id)) != unresolved.get(call_id):
                    fail("responses_unpaired_tool_result")
                inputs.append({"type": "function_call_output", "call_id": call_id,
                               "output": bounded_text(message.get("content"), 524288)})
                del unresolved[call_id]
                continue
            if unresolved:
                fail("responses_missing_tool_result")
            if role == "assistant":
                key = assistant_key(message)
                raw = self._replay.get(key)
                if raw is not None:
                    inputs.extend(copy.deepcopy(raw))
                    for item in raw:
                        if item.get("type") == "function_call":
                            call_id = item["call_id"]
                            if call_id in history_calls:
                                fail("responses_duplicate_call_id")
                            history_calls.add(call_id)
                            unresolved[call_id] = item["name"]
                    continue
                if message.get("tool_calls"):
                    fail("responses_untrusted_call_history")
                inputs.append({"role": "assistant", "content": bounded_text(message.get("content") or "")})
            elif role in ("system", "developer", "user"):
                inputs.append({"role": "developer" if role == "system" else role,
                               "content": bounded_text(message.get("content"), 524288)})
            else:
                fail("responses_invalid_history")
        if unresolved:
            fail("responses_missing_tool_result")
        wire = {"model": self.model, "input": inputs, "store": False, "stream": True}
        if "response_format" in payload:
            wire["text"] = {"format": structured_text_format(payload["response_format"],validated_final_units=self.validated_final_units)}
        if functions:
            wire["tools"] = [{"type": "namespace", "name": self.namespace,
                              "description": "ME3 permitted host-guarded functions.", "tools": functions}]
            wire["tool_choice"] = "auto"
            wire["parallel_tool_calls"] = False
        # Deliberately omit unsupported max_tokens/temperature/etc. No token cap is implied.
        dumps(wire)
        self._pending = names
        return wire

    def decode_completed(self, response):
        if self._pending is None:
            fail("responses_no_pending_request")
        if not isinstance(response, dict) or response.get("status") != "completed" or response.get("error") is not None or response.get("incomplete_details") is not None:
            fail("responses_not_completed")
        if response.get("model") != self.model:
            fail("responses_model_mismatch")
        bounded_text(response.get("id"), 256)
        outputs = response.get("output")
        if not isinstance(outputs, list) or len(outputs) > 64:
            fail("responses_invalid_output")
        text, calls, ids, item_ids = [], [], set(), set()
        for item in outputs:
            if not isinstance(item, dict):
                fail("responses_invalid_output")
            item_id = bounded_text(item.get("id"), 256)
            if not item_id or item_id in item_ids:
                fail("responses_duplicate_output_id")
            item_ids.add(item_id)
            kind = item.get("type")
            if item.get("status", "completed") != "completed":
                fail("responses_incomplete_output")
            if kind == "reasoning":
                # Retain opaque item unchanged privately; never project hidden content.
                dumps(item)
            elif kind == "message":
                if item.get("role") != "assistant" or not isinstance(item.get("content"), list):
                    fail("responses_invalid_output")
                for part in item["content"]:
                    if not isinstance(part, dict) or part.get("type") != "output_text":
                        fail("responses_refusal_or_unsupported_output")
                    text.append(bounded_text(part.get("text")))
            elif kind == "function_call":
                name = item.get("name")
                if not isinstance(name, str) or item.get("namespace") != self.namespace or name not in self._pending or item.get("caller", {"type": "direct"}) != {"type": "direct"}:
                    fail("responses_unadvertised_function")
                call_id = bounded_text(item.get("call_id"), 256)
                if not call_id or call_id in ids or call_id in self._seen_calls:
                    fail("responses_duplicate_call_id")
                # Parsing allowance only: the discovered host workspace contract
                # still owns permission, schema, UTF-8 file limits and dispatch.
                maximum = 400000 if name == "workspace__write_workspace_file" else 12000
                args = bounded_text(item.get("arguments"), maximum)
                if not isinstance(loads(args), dict):
                    fail("responses_invalid_function_arguments")
                ids.add(call_id)
                calls.append({"id": call_id, "type": "function",
                              "function": {"name": name, "arguments": args}})
            else:
                fail("responses_unsupported_output")
        if len(calls) > 3 or len("".join(text)) > 40000 or not (calls or text):
            fail("responses_invalid_output")
        usage = response.get("usage")
        if not isinstance(usage, dict):
            fail("responses_invalid_usage")
        input_tokens, output_tokens, total = (integer(usage.get(k)) for k in ("input_tokens", "output_tokens", "total_tokens"))
        details = usage.get("output_tokens_details") or {}
        if not isinstance(details, dict):
            fail("responses_invalid_usage")
        reasoning = integer(details.get("reasoning_tokens", 0))
        if total != input_tokens + output_tokens or reasoning > output_tokens:
            fail("responses_invalid_usage")
        message = {"role": "assistant", "content": "".join(text) or None}
        if calls:
            message["tool_calls"] = calls
        key = assistant_key(message)
        if key in self._replay and self._replay[key] != outputs:
            fail("responses_ambiguous_history")
        self._replay[key] = copy.deepcopy(outputs)
        self._seen_calls.update(ids)
        self._pending = None
        normalized_usage = {"prompt_tokens": input_tokens, "completion_tokens": output_tokens,
                            "total_tokens": total, "completion_tokens_details": {"reasoning_tokens": reasoning}}
        # Optional diagnostic metadata never changes response acceptance. Keep
        # missing/invalid cache counts absent rather than fabricating zero.
        input_details = usage.get("input_tokens_details")
        cached = input_details.get("cached_tokens") if isinstance(input_details, dict) else None
        if type(cached) is int and 0 <= cached <= input_tokens:
            normalized_usage["prompt_tokens_details"] = {"cached_tokens": cached}
        return {"id": response["id"], "model": self.model,
                "choices": [{"index": 0, "finish_reason": "tool_calls" if calls else "stop", "message": message}],
                "usage": normalized_usage}


def output_identity(item):
    if not isinstance(item,dict) or not isinstance(item.get('type'),str):fail('responses_invalid_output')
    return (item.get('type'),item.get('id'),item.get('call_id'))

def semantic_output(item):
    """Compare execution/visible content exactly, not optional transport metadata."""
    result={k:item.get(k) for k in ('type','id','call_id','role','phase')}
    result['status']=item.get('status','completed')
    if item.get('type')=='message':
        content=item.get('content')
        if not isinstance(content,list):fail('responses_invalid_output')
        result['content']=[{k:part.get(k) for k in ('type','text','refusal')} if isinstance(part,dict) else None for part in content]
    elif item.get('type')=='function_call':
        result.update({k:item.get(k) for k in ('namespace','name','arguments')});result['caller']=item.get('caller',{'type':'direct'})
    else:
        # Opaque reasoning is retained unchanged; do not rewrite encrypted data.
        result=item
    return result

class FinalUnitParser:
    """Default-off incremental syntax observations. Never releases text/audio.

    ENVELOPE_CLOSED is not FULL_COMPLETE. A caller must retain native terminal
    validation, goal/generation fences and child closure. Observed != committed.
    """
    def __init__(self, *, enabled=False, available=('en','es')):
        if not isinstance(available,(tuple,list)) or tuple(available) not in (('en',),('en','es')):fail('final_units_invalid_languages')
        self.enabled=enabled is True;self.available=tuple(available)
        self._raw_parts=[];self._raw_cache='';self._wire_bytes=0;self._buffer=''
        self._object_parts=[];self._object_cursor=0;self._object_stack=[];self._object_quoted=False;self._object_escaped=False;self._scanned_total=0
        self.units=[];self.failed=False;self.started=False
        self.state='PREFIX';self.phase=None;self._position=0;self._tail_position=0
        self._decoder=codecs.getincrementaldecoder('utf-8')('strict')

    @property
    def raw(self):
        # Exact public text view, materialized only when terminal validation or
        # an explicit caller reads it. Empty deltas allocate no raw pieces.
        if self._raw_cache is None:self._raw_cache=''.join(self._raw_parts)
        return self._raw_cache

    @property
    def buffer(self):
        # Preserve the old observable pending-buffer text without forcing the
        # internal scanner to rebuild an unfinished object for every delta.
        return ''.join(self._object_parts)+self._buffer

    def _token(self,token):
        self._buffer=self._buffer.lstrip(' \t\r\n')
        if len(self._buffer)<len(token):
            if not token.startswith(self._buffer):fail('final_units_invalid_structure')
            return False
        if not self._buffer.startswith(token):fail('final_units_invalid_structure')
        self._buffer=self._buffer[len(token):]
        return True

    def _object_end(self):
        # Each arriving object fragment is visited once. Quote, escape and
        # nesting state survive a split, including immediately after backslash.
        for index,char in enumerate(self._buffer):
            self._object_cursor+=1
            if self._object_quoted:
                if self._object_escaped:self._object_escaped=False
                elif char=='\\':self._object_escaped=True
                elif char=='"':self._object_quoted=False
                continue
            if char=='"':self._object_quoted=True
            elif char in '{[':self._object_stack.append(char)
            elif char in '}]':
                if not self._object_stack or (self._object_stack.pop(),char) not in (('{','}'),('[',']')):
                    fail('final_units_invalid_structure')
                if not self._object_stack:return index+1
        return None

    def _object_text(self,end):
        return ''.join(self._object_parts)+self._buffer[:end]

    def _consume_object(self,end):
        self._object_parts=[];self._buffer=self._buffer[end:]
        self._scanned_total+=self._object_cursor;self._object_cursor=0
        self._object_stack=[];self._object_quoted=False;self._object_escaped=False

    def feed(self,chunk):
        if not self.enabled:fail('final_units_disabled')
        if self.failed:fail('final_units_failed')
        try:
            if not isinstance(chunk,bytes):fail('final_units_bytes_required')
            try:text=self._decoder.decode(chunk,final=False)
            except UnicodeError:fail('final_units_invalid_UTF8')
            return self.feed_text(text)
        except BaseException:
            self.failed=True;self.state='FAILED';raise

    def feed_text(self,text):
        if not self.enabled:fail('final_units_disabled')
        if self.failed:fail('final_units_failed')
        observations=[]
        try:
            bounded_text(text,262144)
            if text:
                self._raw_parts.append(text);self._raw_cache=None
            self._buffer+=text;self._wire_bytes+=len(text.encode('utf-8',errors='strict'))
            if self._wire_bytes>262144:fail('final_units_wire_bound')
            prefix=('{','"phase"',':',None,',','"units"',':','[')
            tail=(',','"complete"',':','true','}')
            while True:
                if self.state=='PREFIX':
                    if self._position==3:
                        self._buffer=self._buffer.lstrip(' \t\r\n')
                        options=('"FINAL"','"WHOLE"')
                        selected=next((token for token in options if self._buffer.startswith(token)),None)
                        if selected is None:
                            if not any(token.startswith(self._buffer) for token in options):fail('final_units_invalid_phase')
                            break
                        self.phase=selected[1:-1];self._buffer=self._buffer[len(selected):]
                    elif not self._token(prefix[self._position]):break
                    self._position+=1
                    if self._position==len(prefix):
                        self.started=True;self.state='UNIT'
                        if self.phase=='FINAL':observations.append({'kind':'header','phase':'FINAL','descriptive_only':True})
                elif self.state=='UNIT':
                    if not self._object_cursor:
                        self._buffer=self._buffer.lstrip(' \t\r\n')
                        if not self._buffer:break
                        if self._buffer[0]!='{':fail('final_units_unit_object_required')
                    end=self._object_end()
                    if end is None:
                        if self._buffer:self._object_parts.append(self._buffer)
                        self._buffer=''
                        break
                    row=_unit(loads(self._object_text(end)),len(self.units),self.available)
                    if sum(len(unit['text']) for unit in self.units)+len(row['text'])>40000:
                        fail('final_units_text_bound')
                    self.units.append(row);self._consume_object(end);self.state='SEPARATOR'
                    if self.phase=='FINAL':observations.append({'kind':'unit','phase':'FINAL',**copy.deepcopy(row),'descriptive_only':True})
                elif self.state=='SEPARATOR':
                    self._buffer=self._buffer.lstrip(' \t\r\n')
                    if not self._buffer:break
                    char=self._buffer[0];self._buffer=self._buffer[1:]
                    if char==',':self.state='UNIT'
                    elif char==']':self.state='TAIL'
                    else:fail('final_units_invalid_structure')
                elif self.state=='TAIL':
                    if not self._token(tail[self._tail_position]):break
                    self._tail_position+=1
                    if self._tail_position==len(tail):
                        self.state='ENVELOPE_CLOSED'
                        # Independently decode the whole object before end observation.
                        _,_,units=parse_final_units(self.raw,self.available)
                        if units!=self.units:fail('final_units_terminal_projection_mismatch')
                        if self.phase=='FINAL':observations.append({'kind':'end','phase':'FINAL','unit_count':len(units),
                                                                   'descriptive_only':True,'native_complete':False})
                elif self.state=='ENVELOPE_CLOSED':
                    if self._buffer.strip():fail('final_units_trailing_output')
                    break
                else:fail('final_units_failed')
            return observations
        except BaseException:
            self.failed=True;self.state='FAILED';raise

    def finish(self,terminal_raw):
        if not self.enabled or self.failed:fail('final_units_failed')
        try:
            try:tail=self._decoder.decode(b'',final=True)
            except UnicodeError:fail('final_units_invalid_UTF8')
            if tail:fail('final_units_unconsumed_UTF8')
            if self.state!='ENVELOPE_CLOSED' or self._buffer.strip():fail('final_units_incomplete')
            if terminal_raw!=self.raw:fail('final_units_terminal_text_mismatch')
            text,segments,units=parse_final_units(terminal_raw,self.available)
            if units!=self.units:fail('final_units_terminal_projection_mismatch')
            return {'text':text,'segments':segments,'units':units,'native_complete':False}
        except BaseException:
            self.failed=True;self.state='FAILED';raise

    def failure_disposition(self,committed_units=0):
        # Host supplies actual commit accounting; observations alone never imply
        # spoken delivery. This is classification, not a commit/complete API.
        if type(committed_units) is not int or not 0<=committed_units<=len(self.units):
            fail('final_units_invalid_commit_count')
        if self.phase=='WHOLE' and committed_units:fail('final_units_WHOLE_cannot_have_early_commits')
        return 'PARTIAL_FAILED' if committed_units else 'FAILED'


class SSEParser:
    """Buffer terminal-only SSE. No deltas or provisional calls escape this parser."""
    def __init__(self, *, max_bytes=MAX_STREAM_BYTES, max_events=MAX_EVENTS, on_event=None,on_diagnostic=None,on_stage=None,finalized_stream_output=False,final_units=None):
        self.max_bytes, self.max_events = max_bytes, max_events
        self._buffer = b""
        self._bytes = self._events = 0
        self._sequence = -1
        self._completed = None
        self._response_id = None
        self._done = {}
        self._added = {}
        self._failed = False
        self._on_event=on_event
        self._event_types=set()
        self._on_diagnostic=on_diagnostic
        self._on_stage=on_stage;self._first_text_seen=False
        self._finalized_stream_output=finalized_stream_output is True
        if final_units is not None and (not isinstance(final_units,FinalUnitParser) or not final_units.enabled):
            fail('final_units_explicit_capability_required')
        self._final_units=final_units;self._final_events=[];self._final_binding=None;self._final_tools_seen=False
        self._final_messages=set();self._final_text_done=False

    def take_final_unit_events(self):
        values=self._final_events;self._final_events=[]
        return copy.deepcopy(values)

    def _observe_final_units(self,event,value,response):
        parser=self._final_units
        if parser is None:return
        item=value.get('item')
        is_tool=(isinstance(item,dict) and item.get('type')=='function_call') or 'function_call' in event
        if event=='response.completed' and isinstance(response,dict):
            is_tool=is_tool or any(isinstance(row,dict) and row.get('type')=='function_call' for row in response.get('output',[]))
        if is_tool:
            if parser.started:fail('final_units_late_tool_call_forbidden')
            self._final_tools_seen=True
        if event=='response.output_item.added' and isinstance(item,dict) and item.get('type')=='message':
            if item.get('role')!='assistant':fail('final_units_assistant_identity_required')
            self._final_messages.add(value['output_index'])
        if event in ('response.output_text.delta','response.output_text.done'):
            if self._final_tools_seen:fail('final_units_mixed_tool_and_final_text')
            index=value.get('output_index');content=value.get('content_index');item_id=value.get('item_id')
            if type(index) is not int or index not in self._final_messages or content!=0 or type(content) is not int:
                fail('final_units_stream_part_identity_required')
            expected=self._added.get(index)
            if expected is None or expected[0]!='message' or expected[1]!=item_id or not isinstance(item_id,str):
                fail('final_units_stream_item_identity_mismatch')
            if self._response_id is None:fail('final_units_stream_response_identity_required')
            binding={'response_id':self._response_id,'item_id':item_id,'output_index':index,'content_index':content}
            if self._final_binding is not None and binding!=self._final_binding:
                fail('final_units_multiple_message_streams_forbidden')
            self._final_binding=binding
            if self._final_text_done:fail('final_units_text_after_done')
            if event=='response.output_text.done':
                if value.get('text')!=parser.raw:fail('final_units_done_text_mismatch')
                self._final_text_done=True
            else:
                for observation in parser.feed_text(value.get('delta')):
                    self._final_events.append({**observation,**binding})
        if event=='response.completed':
            messages=[row for row in response['output'] if isinstance(row,dict) and row.get('type')=='message']
            if parser.started:
                if len(messages)!=1 or self._final_binding is None or messages[0].get('id')!=self._final_binding['item_id']:
                    fail('final_units_terminal_message_identity_mismatch')
                parts=messages[0].get('content')
                if not isinstance(parts,list) or len(parts)!=1 or not isinstance(parts[0],dict) or parts[0].get('type')!='output_text':
                    fail('final_units_terminal_single_part_required')
                parser.finish(parts[0].get('text'))
            elif messages:
                # No raw terminal-only text is retroactively treated as an early unit.
                fail('final_units_missing_streamed_header')

    def feed(self, chunk):
        if self._failed:
            fail("responses_stream_failed")
        try:
            self._feed(chunk)
        except BaseException:
            self._failed = True
            if self._final_units is not None:self._final_units.failed=True;self._final_units.state='FAILED'
            raise

    def _feed(self, chunk):
        if not isinstance(chunk, bytes):
            fail("responses_invalid_stream_chunk")
        self._bytes += len(chunk)
        if self._bytes > self.max_bytes:
            fail("responses_stream_byte_limit")
        self._buffer += chunk
        while True:
            match = re.search(br"\r?\n\r?\n", self._buffer)
            if match is None:
                if len(self._buffer) > MAX_EVENT_BYTES:
                    fail("responses_stream_event_limit")
                return
            frame, self._buffer = self._buffer[:match.start()], self._buffer[match.end():]
            self._frame(frame)

    def _frame(self, frame):
        if len(frame) > MAX_EVENT_BYTES:
            fail("responses_stream_event_limit")
        try:
            lines = frame.decode("utf-8", errors="strict").splitlines()
        except UnicodeError:
            fail("responses_invalid_stream_utf8")
        event, data = None, []
        for line in lines:
            if not line or line.startswith(":"):
                continue
            field, sep, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if not sep or field not in ("event", "data"):
                fail("responses_invalid_sse_field")
            if field == "event":
                if event is not None:
                    fail("responses_duplicate_sse_event")
                event = value
            else:
                data.append(value)
        if not data and event is None:
            return
        if self._completed is not None:
            fail("responses_event_after_completion")
        self._events += 1
        if self._events > self.max_events:
            fail("responses_stream_event_count_limit")
        value = loads("\n".join(data))
        if not isinstance(value, dict) or value.get("type") != event:
            fail("responses_sse_type_mismatch")
        sequence = value.get("sequence_number")
        if type(sequence) is not int or sequence <= self._sequence:
            fail("responses_invalid_stream_sequence")
        self._sequence = sequence
        if not isinstance(event,str) or len(event)>128 or (event!='error' and re.fullmatch(r'response\.[a-z0-9_]+(?:\.[a-z0-9_]+)*',event) is None):
            fail("responses_unsupported_stream_event")
        if event not in self._event_types:
            if len(self._event_types)>=64:fail('responses_event_type_limit')
            self._event_types.add(event)
            if self._on_event is not None:self._on_event(event)
        if event == 'error' or set(event.split('.')) & {'error','fault','refusal','failed','incomplete','cancelled','canceled','aborted','rejected','expired','terminated','timeout'}:
            fail("responses_stream_failed")
        # New informational events have no authority. Only the exact completed
        # event below can set final output; the codec independently validates it.
        for control in (value,value.get('response'),value.get('item')):
            if isinstance(control,dict):
                if control.get('error') is not None or control.get('incomplete_details') is not None:fail('responses_stream_failed')
                if 'status' in control and (not isinstance(control['status'],str) or control['status'] not in {'queued','in_progress','completed'}):fail('responses_stream_failed')
        explicit=value.get('response_id')
        if explicit is not None:
            if not isinstance(explicit,str) or not 1<=len(explicit)<=256 or self._response_id not in (None,explicit):fail('responses_stream_identity_mismatch')
            self._response_id=explicit
        response = value.get("response")
        if response is not None:
            if not isinstance(response, dict):
                fail("responses_invalid_output")
            rid = response.get("id")
            if not isinstance(rid, str) or not 1<=len(rid)<=256 or self._response_id not in (None, rid):
                fail("responses_stream_identity_mismatch")
            self._response_id = rid
        if event == 'response.output_item.added' and value.get('item') is not None:
            index=value.get('output_index')
            if type(index) is not int or not 0<=index<64 or index in self._added:fail('responses_duplicate_output_item')
            self._added[index]=output_identity(value['item'])
        if event == "response.output_item.done":
            index = value.get("output_index")
            if type(index) is not int or not 0<=index<64 or index in self._done:
                fail("responses_duplicate_output_item")
            if index in self._added and output_identity(value.get('item'))!=self._added[index]:fail('responses_stream_output_identity_mismatch')
            self._done[index] = value.get("item")
        if event == "response.completed":
            if not isinstance(response, dict) or response.get("status") != "completed" or response.get("error") is not None or response.get("incomplete_details") is not None:
                fail("responses_not_completed")
            output = response.get("output")
            kind='absent' if 'output' not in response else 'null' if output is None else 'list' if isinstance(output,list) else 'invalid'
            if self._on_diagnostic is not None:self._on_diagnostic(dict(terminal_output_kind=kind,terminal_output_count=len(output) if isinstance(output,list) else None,added_items=len(self._added),finalized_items=len(self._done)))
            if output is None or self._finalized_stream_output and output==[] and self._done:
                if output==[] and set(self._done)!=set(self._added):fail('responses_unannounced_output')
                if any(index not in self._done for index in self._added) or not self._done or sorted(self._done)!=list(range(len(self._done))):fail('responses_unfinished_output')
                if output==[] and self._on_diagnostic is not None:self._on_diagnostic(dict(reconstruction='terminal_output_empty_stream_finalized'))
                response=copy.deepcopy(response);output=[copy.deepcopy(self._done[i]) for i in sorted(self._done)];response['output']=output
            if not isinstance(output,list) or len(output)>64:
                fail("responses_stream_output_mismatch")
            for index,identity in self._added.items():
                if index>=len(output) or output_identity(output[index])!=identity:fail('responses_stream_output_identity_mismatch')
            for index,item in self._done.items():
                if index>=len(output) or output_identity(output[index])!=output_identity(item) or semantic_output(output[index])!=semantic_output(item):fail('responses_stream_output_mismatch')
            self._completed = response
            if self._on_stage is not None:self._on_stage('response_completed_received')
        if event=='response.output_text.delta' and type(value.get('delta')) is str and value['delta'] and not self._first_text_seen:
            self._first_text_seen=True
            if self._on_stage is not None:self._on_stage('first_output_text_delta_received')
        self._observe_final_units(event,value,response)

    def finish(self):
        if self._failed or self._buffer.strip() or self._completed is None:
            fail("responses_stream_incomplete")
        return copy.deepcopy(self._completed)


async def read_sse(chunks, close, *, stop_requested=None, max_bytes=MAX_STREAM_BYTES, max_events=MAX_EVENTS,on_event=None,on_diagnostic=None,on_stage=None,finalized_stream_output=False,final_units=None,on_final_unit=None):
    """Caller owns read timeouts/task cancellation. Close on every exit, never retry."""
    try:
        if (final_units is None)!=(on_final_unit is None) or on_final_unit is not None and not callable(on_final_unit):
            fail('final_units_observer_capability_mismatch')
        parser = SSEParser(max_bytes=max_bytes, max_events=max_events,on_event=on_event,on_diagnostic=on_diagnostic,on_stage=on_stage,finalized_stream_output=finalized_stream_output,final_units=final_units)
        if stop_requested is not None and stop_requested():
            raise asyncio.CancelledError()
        async for chunk in chunks:
            if stop_requested is not None and stop_requested():
                raise asyncio.CancelledError()
            parser.feed(chunk)
            if on_final_unit is not None:
                for observation in parser.take_final_unit_events():
                    if stop_requested is not None and stop_requested():raise asyncio.CancelledError()
                    accepted=on_final_unit(observation)
                    if not inspect.isawaitable(accepted):fail('final_units_async_observer_required')
                    accepted=await asyncio.wait_for(accepted,timeout=5)
                    if accepted is not True:fail('final_units_observer_declined')
                    if stop_requested is not None and stop_requested():raise asyncio.CancelledError()
        if stop_requested is not None and stop_requested():
            raise asyncio.CancelledError()
        if on_stage is not None:on_stage('stream_eof')
        return parser.finish()
    finally:
        result = close()
        if inspect.isawaitable(result):
            await result
