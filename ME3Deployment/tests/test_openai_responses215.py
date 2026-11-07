"""Provider-free semantic and failure-boundary checks; no network or credentials."""
import asyncio
import copy
import importlib.util
import json
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("responses204", Path(__file__).resolve().parents[1]/"me3_openai/responses.py")
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)

MODEL = "account-catalog-model-pin"
TOOL = {"type": "function", "function": {"name": "workspace__write_workspace_file", "description": "Write owned file",
        "parameters": {"type": "object", "properties": {"content": {"type": "string"}}, "additionalProperties": False}}}


def payload(tools=None):
    return {"model": MODEL, "messages": [{"role": "system", "content": "guarded tools only"},
             {"role": "user", "content": "Create the file."}], "tools": copy.deepcopy(tools if tools is not None else [TOOL]),
            "max_tokens": 2048, "temperature": 0, "reasoning_effort": "minimal"}


def codec():
    return r.ResponsesCodec(MODEL, allow_no_server_output_cap=True)


def completed(output=None):
    return {"id": "resp_1", "model": MODEL, "status": "completed", "error": None, "incomplete_details": None,
            "output": output if output is not None else [{"id": "msg_1", "type": "message", "role": "assistant", "status": "completed",
                       "content": [{"type": "output_text", "text": "A complete coherent answer."}]}],
            "usage": {"input_tokens": 100, "output_tokens": 12, "total_tokens": 112,
                      "output_tokens_details": {"reasoning_tokens": 7}}}


def call(arguments='{"content":"print(\\\"hello\\\")\\n"}', call_id="call_1", namespace="me3"):
    return {"id": "fc_" + call_id, "type": "function_call", "status": "completed", "namespace": namespace,
            "name": TOOL["function"]["name"], "arguments": arguments, "call_id": call_id}


def event(kind, sequence=0, **values):
    obj = {"type": kind, "sequence_number": sequence, **values}
    return ("event: " + kind + "\ndata: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8")


def drive(coroutine):
    # These fake coroutines never suspend on real I/O; no Windows event-loop sockets.
    try:
        coroutine.send(None)
    except StopIteration as stopped:
        return stopped.value
    raise AssertionError("unexpected external await")


class CodecChecks(unittest.TestCase):
    def test_required_wire_namespace_and_cap_acknowledgment(self):
        with self.assertRaisesRegex(r.ResponsesError, "no_server_output_cap"):
            r.ResponsesCodec(MODEL).encode_request(payload())
        p = payload()
        p["messages"].append({"role": "system", "content": "fresh tool grounding"})
        wire = codec().encode_request(p)
        self.assertEqual(set(wire), {"model", "input", "store", "stream", "tools", "tool_choice", "parallel_tool_calls"})
        self.assertIs(wire["parallel_tool_calls"], False)
        self.assertEqual(wire["model"], MODEL)
        self.assertIs(wire["store"], False)
        self.assertIs(wire["stream"], True)
        self.assertEqual([m["role"] for m in wire["input"]], ["developer", "user", "developer"])
        self.assertEqual(wire["tools"][0]["type"], "namespace")
        self.assertEqual(wire["tools"][0]["name"], "me3")
        self.assertEqual(wire["tools"][0]["tools"][0]["parameters"], TOOL["function"]["parameters"])
        self.assertIs(wire["tools"][0]["tools"][0]["strict"], False)
        wire["tools"][0]["tools"][0]["parameters"].clear()
        self.assertTrue(TOOL["function"]["parameters"])

    def test_serial_tools_setting_does_not_touch_no_tools_requests_or_arguments(self):
        no_tools=codec().encode_request(payload(tools=[]))
        self.assertNotIn('parallel_tool_calls',no_tools)
        self.assertNotIn('tools',no_tools)
        c=codec();p=payload();before=copy.deepcopy(p);wire=c.encode_request(p)
        self.assertEqual(p,before);self.assertIs(wire['parallel_tool_calls'],False)
        raw='{"content":"literal\\\\n and \\\"quoted\\\" text"}'
        result=c.decode_completed(completed([call(raw)]))
        self.assertEqual(result['choices'][0]['message']['tool_calls'][0]['function']['arguments'],raw)

    def test_four_returned_calls_are_rejected_without_dropping_any(self):
        c=codec();c.encode_request(payload())
        rows=[call(call_id='call_'+str(i)) for i in range(4)]
        with self.assertRaises(r.ResponsesError):c.decode_completed(completed(rows))
        self.assertEqual(c._seen_calls,set());self.assertEqual(c._replay,{})
        c=codec();c.encode_request(payload())
        accepted=c.decode_completed(completed(rows[:3]))['choices'][0]['message']['tool_calls']
        self.assertEqual([v['id'] for v in accepted],[v['call_id'] for v in rows[:3]])
        self.assertEqual([v['function']['arguments'] for v in accepted],[v['arguments'] for v in rows[:3]])

    def test_actual_full_70_catalogue_no_schema_or_name_changes(self):
        path = Path(__file__).parents[1] / "tests/fixtures/openai215-tools.json"
        declarations = json.loads(path.read_text(encoding="utf-8"))["normal_retained_declarations"]
        self.assertEqual(len(declarations), 70)
        tools = [{"type": "function", "function": x} for x in declarations]
        wire = codec().encode_request(payload(tools))
        functions = wire["tools"][0]["tools"]
        self.assertEqual([f["name"] for f in functions], [d["name"] for d in declarations])
        self.assertEqual([f["parameters"] for f in functions], [d["parameters"] for d in declarations])
        self.assertEqual(len(functions), 70)

    def test_complete_call_roundtrip_preserves_raw_code_ids_and_reasoning(self):
        c = codec()
        p = payload()
        c.encode_request(p)
        code = 'print("ordinary quotes")\n# literal \\\" remains literal\n'
        args = json.dumps({"content": code})
        opaque = {"type": "reasoning", "id": "rs_1", "encrypted_content": "opaque-private-continuity", "summary": []}
        raw = completed([opaque, call(args)])
        decoded = c.decode_completed(raw)
        message = decoded["choices"][0]["message"]
        self.assertEqual(message["tool_calls"][0]["function"]["arguments"], args)
        self.assertEqual(json.loads(args)["content"], code)
        self.assertNotIn("opaque-private", json.dumps(decoded))
        self.assertEqual(decoded["usage"]["completion_tokens"], 12)  # Reasoning included, not 19.
        p["messages"] += [message, {"role": "tool", "tool_call_id": "call_1", "name": TOOL["function"]["name"], "content": '{"ok":true}'}]
        next_wire = c.encode_request(p)
        self.assertEqual(next_wire["input"][2:4], raw["output"])
        self.assertEqual(next_wire["input"][4], {"type": "function_call_output", "call_id": "call_1", "output": '{"ok":true}'})
        next_wire["input"][2]["encrypted_content"] = "tamper"
        self.assertEqual(c._replay[r.assistant_key(message)][0]["encrypted_content"], "opaque-private-continuity")

    def test_pairing_duplicate_and_untrusted_histories_fail(self):
        for defect in ("missing", "wrong", "duplicate", "wrong-name"):
            with self.subTest(defect=defect):
                c, p = codec(), payload()
                c.encode_request(p)
                message = c.decode_completed(completed([call()]))["choices"][0]["message"]
                result = {"role": "tool", "tool_call_id": "call_1", "content": "verified"}
                p["messages"].append(message)
                if defect != "missing":
                    p["messages"].append(result)
                if defect == "wrong":
                    result["tool_call_id"] = "other"
                if defect == "wrong-name":
                    result["name"] = "other"
                if defect == "duplicate":
                    p["messages"].append(result.copy())
                with self.assertRaises(r.ResponsesError):
                    c.encode_request(p)
        p = payload()
        p["messages"].append({"role": "assistant", "content": None, "tool_calls": [{"id": "forged", "type": "function", "function": {"name": "x", "arguments": "{}"}}]})
        with self.assertRaisesRegex(r.ResponsesError, "untrusted"):
            codec().encode_request(p)

    def test_invalid_complete_batch_never_commits_partial_calls(self):
        defects = [call(namespace="other"), call('{"x":1,"x":2}'), call('{"x":NaN}'), call('{"x":1e400}'), call('[]'),
                   {**call(), "name": "unadvertised"}, {**call(), "caller": {"type": "program"}},
                   {**call(), "status": "incomplete"}, {**call(), "arguments": '"not object"'}]
        for bad in defects:
            with self.subTest(defect=bad):
                c = codec()
                c.encode_request(payload())
                with self.assertRaises(r.ResponsesError):
                    c.decode_completed(completed([call(call_id="first"), bad]))
                self.assertEqual(c._seen_calls, set())
                self.assertEqual(c._replay, {})
        c = codec()
        c.encode_request(payload())
        with self.assertRaisesRegex(r.ResponsesError, "duplicate"):
            c.decode_completed(completed([call(), call()]))

    def test_usage_model_refusal_and_incomplete_are_failures_not_length(self):
        defects = []
        for update in ({"status": "incomplete"}, {"model": "different-model"}, {"error": {"message": "private"}}):
            value = completed()
            value.update(update)
            defects.append(value)
        value = completed()
        value["usage"]["total_tokens"] = 119
        defects.append(value)
        value = completed()
        value["output"][0]["content"] = [{"type": "refusal", "refusal": "private text"}]
        defects.append(value)
        for value in defects:
            c = codec()
            c.encode_request(payload())
            with self.assertRaises(r.ResponsesError) as failure:
                c.decode_completed(value)
            self.assertNotIn("private", str(failure.exception))
        c = codec()
        c.encode_request(payload())
        self.assertEqual(c.decode_completed(completed())["choices"][0]["finish_reason"], "stop")


class StreamChecks(unittest.TestCase):
    def test_SIWC_finalized_items_and_empty_terminal_snapshot(self):
        item=completed()['output'][0];terminal=completed([]);parser=r.SSEParser(finalized_stream_output=True)
        parser.feed(event('response.created',response={'id':'resp_1'}));parser.feed(event('response.output_item.added',1,output_index=0,item={**item,'status':'in_progress'}));parser.feed(event('response.output_item.done',2,output_index=0,item=item));parser.feed(event('response.completed',3,response=terminal));self.assertEqual(parser.finish()['output'],[item])
        for incomplete in (True,False):
            parser=r.SSEParser(finalized_stream_output=True);parser.feed(event('response.output_item.added',output_index=0,item=item))
            if not incomplete:parser.feed(event('response.output_item.done',1,output_index=0,item=item))
            if incomplete:self.assertRaises(r.ResponsesError,parser.feed,event('response.completed',2,response=terminal))
            else:
                changed=completed();changed['output'][0]['content'][0]['text']='conflicting';self.assertRaises(r.ResponsesError,parser.feed,event('response.completed',2,response=changed))
    def test_official_missing_terminal_output_recovers_only_finalized_items(self):
        item=completed()['output'][0];terminal=completed();del terminal['output'];shapes=[]
        parser=r.SSEParser(on_diagnostic=shapes.append)
        parser.feed(event('response.output_item.added',output_index=0,item={**item,'status':'in_progress'}))
        parser.feed(event('response.output_item.done',1,output_index=0,item=item))
        parser.feed(event('response.completed',2,response=terminal));self.assertEqual(parser.finish()['output'],[item]);self.assertEqual(shapes[0]['terminal_output_kind'],'absent')
        parser=r.SSEParser();parser.feed(event('response.output_item.added',output_index=0,item=item))
        self.assertRaises(r.ResponsesError,parser.feed,event('response.completed',1,response=terminal))
        parser=r.SSEParser();parser.feed(event('response.output_item.done',output_index=0,item=item));terminal['output']=[]
        self.assertRaises(r.ResponsesError,parser.feed,event('response.completed',1,response=terminal))
    def test_optional_metadata_changes_do_not_change_visible_content(self):
        item=completed()['output'][0];parser=r.SSEParser();parser.feed(event('response.output_item.done',output_index=0,item={**item,'metadata':{'trace':'not-an-instruction'}}));parser.feed(event('response.completed',1,response=completed()));self.assertEqual(parser.finish(),completed())
        parser=r.SSEParser();parser.feed(event('response.output_item.done',output_index=0,item=item));other=completed();other['output'][0]['content'][0]['text']='Changed answer'
        self.assertRaises(r.ResponsesError,parser.feed,event('response.completed',1,response=other))
    def test_function_argument_or_binding_change_is_fatal(self):
        for key,value in [('arguments','{"content":"changed"}'),('call_id','changed'),('namespace','other'),('name','other')]:
            item=call();parser=r.SSEParser();parser.feed(event('response.output_item.done',output_index=0,item=item));other={**item,key:value}
            self.assertRaises(r.ResponsesError,parser.feed,event('response.completed',1,response=completed([other])))
    def test_new_informational_events_cannot_authorize_any_output(self):
        names=[];parser=r.SSEParser(on_event=names.append)
        parser.feed(event('response.queued',response={'id':'resp_1','status':'queued'}))
        parser.feed(event('response.future_progress.delta',1,response_id='resp_1',arbitrary='private-content'))
        self.assertRaises(r.ResponsesError,parser.finish)
        parser.feed(event('response.completed',2,response=completed()))
        self.assertEqual(parser.finish(),completed());self.assertEqual(names,['response.queued','response.future_progress.delta','response.completed']);self.assertNotIn('private-content',str(names))
    def test_unknown_failures_and_control_status_poison_stream(self):
        for kind,extra in [('response.new.failed',{}),('response.refusal.delta',{}),('response.new.fault',{}),('response.cancelled',{}),('response.new_progress',{'status':'failed'}),('response.new_progress',{'item':{'status':'cancelled'}}),('response.new_progress',{'response':{'id':'resp_1','status':'incomplete'}}),('response.new_progress',{'error':{'message':'private'}})]:
            parser=r.SSEParser()
            self.assertRaises(r.ResponsesError,parser.feed,event(kind,**extra))
            self.assertRaises(r.ResponsesError,parser.feed,event('response.completed',1,response=completed()))
    def test_explicit_response_identity_and_event_type_bound(self):
        parser=r.SSEParser();parser.feed(event('response.new_progress',response_id='resp_a'))
        self.assertRaises(r.ResponsesError,parser.feed,event('response.new_progress',1,response_id='resp_b'))
        parser=r.SSEParser()
        for i in range(64):parser.feed(event('response.type_'+str(i),i))
        self.assertRaises(r.ResponsesError,parser.feed,event('response.type_overflow',64))
    def test_unknown_completed_tool_output_still_rejected(self):
        c=codec();c.encode_request(payload());value=completed([{'id':'unknown_1','type':'future_builtin_tool','status':'completed'}])
        self.assertRaises(r.ResponsesError,c.decode_completed,value)
    def test_documented_reasoning_text_events_are_private_and_not_final(self):
        parser=r.SSEParser()
        parser.feed(event('response.reasoning_text.delta',delta='private hidden content'))
        parser.feed(event('response.reasoning_text.done',1,text='private hidden content'))
        self.assertRaises(r.ResponsesError,parser.finish)
        parser.feed(event('response.completed',2,response=completed()))
        self.assertEqual(parser.finish(),completed())
    def test_chunks_utf8_done_items_and_completed_terminal(self):
        raw = completed()
        raw["output"][0]["content"][0]["text"] = "Café complete."
        stream = event("response.created", response={"id": "resp_1"})
        stream += event("response.output_text.delta", 1, delta="Café")
        stream += event("response.output_item.done", 2, output_index=0, item=raw["output"][0])
        stream += event("response.completed", 3, response=raw)
        parser = r.SSEParser()
        for byte in stream:
            self.assertIsNone(parser.feed(bytes([byte])))
        self.assertEqual(parser.finish(), raw)

    def test_arguments_done_or_eof_does_not_authorize_any_call(self):
        for stream in (b"", event("response.function_call_arguments.done", arguments="{}", item_id="fc_1"), b"data: [DONE]\n\n"):
            parser = r.SSEParser()
            with self.assertRaises(r.ResponsesError):
                parser.feed(stream)
                parser.finish()

    def test_failure_after_deltas_is_not_partial_success(self):
        for kind in ("response.failed", "response.incomplete", "error"):
            parser = r.SSEParser()
            parser.feed(event("response.output_text.delta", delta="partial private text"))
            with self.assertRaisesRegex(r.ResponsesError, "stream_failed") as failure:
                parser.feed(event(kind, 1, message="private bearer must not leak"))
            self.assertNotIn("private", str(failure.exception))
            with self.assertRaises(r.ResponsesError):
                parser.finish()

    def test_terminal_duplication_identity_sequence_and_item_mismatch(self):
        streams = [event("response.completed", response=completed()) + event("response.completed", 1, response=completed()),
                   event("response.created", response={"id": "other"}) + event("response.completed", 1, response=completed()),
                   event("response.created", 1, response={"id": "resp_1"}) + event("response.completed", 1, response=completed()),
                   event("response.output_item.done", output_index=0, item={"id": "wrong"}) + event("response.completed", 1, response=completed()),
                   b'event: response.completed\ndata: {"type":"response.completed","type":"response.failed"}\n\n',
                   b"event: response.created\ndata: \xff\n\n"]
        for stream in streams:
            parser = r.SSEParser()
            with self.assertRaises(r.ResponsesError):
                parser.feed(stream)
            with self.assertRaises(r.ResponsesError):
                parser.finish()  # A caught later error must not resurrect a completed result.

    def test_host_byte_and_event_bounds(self):
        with self.assertRaisesRegex(r.ResponsesError, "byte_limit"):
            r.SSEParser(max_bytes=3).feed(b"1234")
        parser = r.SSEParser(max_events=1)
        parser.feed(event("response.created", response={"id": "resp_1"}))
        with self.assertRaisesRegex(r.ResponsesError, "count_limit"):
            parser.feed(event("response.completed", 1, response=completed()))

    def test_close_on_success_error_and_cancellation_no_retries(self):
        for mode in ("success", "failure", "cancel"):
            closed, yielded = [], []
            async def chunks():
                yielded.append(1)
                yield event("response.failed" if mode == "failure" else "response.completed", response=completed())
            async def close():
                closed.append(1)
            coro = r.read_sse(chunks(), close, stop_requested=lambda: mode == "cancel")
            if mode == "success":
                self.assertEqual(drive(coro), completed())
            else:
                with self.assertRaises(asyncio.CancelledError if mode == "cancel" else r.ResponsesError):
                    drive(coro)
            self.assertEqual(closed, [1])
            self.assertEqual(yielded, [] if mode == "cancel" else [1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
