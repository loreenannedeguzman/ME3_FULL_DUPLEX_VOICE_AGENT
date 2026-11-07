"""Pure actual codec/protocol/Gateway contracts; no provider, credential or file writes."""
import asyncio, copy, importlib.util, json, os, sys, unittest
from pathlib import Path
from types import SimpleNamespace
from jsonschema.exceptions import ValidationError

ROOT=Path(os.environ.get('ME3_PROJECT_ROOT',Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(ROOT))
OVERLAY=Path(__file__).resolve().parents[1]
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m
codec_module=load('responses_wire232',OVERLAY/'me3_openai/responses.py')
protocol=load('protocol_wire232',OVERLAY/'onit_candidate/protocol.py')
gateway_module=load('gateway_wire232',OVERLAY/'onit_candidate/gateway.py')
registry_module=load('registry_wire232',OVERLAY/'me3_assistant/registry.py')
workspace_module=load('workspace_wire232',OVERLAY/'me3_assistant/workspace.py')
ALIAS='workspace__write_workspace_file';MODEL='gpt-6.1-sol'
SCHEMA={'type':'object','properties':{'path':{'type':'string'},'text':{'type':'string'},'expected_sha256':{'type':'string'}},'required':['path','text'],'additionalProperties':False}

def payload(name=ALIAS):
    return {'model':MODEL,'messages':[{'role':'user','content':'Create my authorized artifact.'}],
            'tools':[{'type':'function','function':{'name':name,'description':'Authored metadata fixture','parameters':copy.deepcopy(SCHEMA)}}]}
def completed(args,name=ALIAS):
    return {'id':'resp232','model':MODEL,'status':'completed','output':[{'id':'fc232','type':'function_call','status':'completed','namespace':'me3','name':name,'call_id':'call232','arguments':args}],
            'usage':{'input_tokens':10,'output_tokens':20,'total_tokens':30,'output_tokens_details':{'reasoning_tokens':0}}}
def make_codec(name=ALIAS):
    c=codec_module.ResponsesCodec(MODEL,allow_no_server_output_cap=True);c.encode_request(payload(name));return c
def native(args,name=ALIAS):return {'id':'call232','type':'function','function':{'name':name,'arguments':args}}
def gate():
    r=object.__new__(registry_module.Registry)
    r.schemas={('workspace','write_workspace_file'):SimpleNamespace(inputSchema=copy.deepcopy(SCHEMA),description='Owned file write')}
    r.sessions={'workspace':(None,{'module':'me3_assistant.workspace_server','identity':'me3','role':'utility','workspace_tools':['write_workspace_file']})}
    r.reads={ALIAS:('workspace','write_workspace_file')};r.workspace_operations={ALIAS};r.session_grant={'workspace_access':True};r.config={'runtime':{'batch_calls':3}};r.calls=0
    g=object.__new__(gateway_module.Gateway);g.registry=r;g.action_declarations=[];g.blockers=lambda:False
    g.goal='authored232';g.generation=1;g.current=lambda generation:generation==1
    return g,r
def drive(coro):
    try:coro.send(None)
    except StopIteration as done:return done.value
    raise AssertionError('unexpected external await')


class WriteWireChecks(unittest.TestCase):
    def test_actual_64KiB_ascii_unicode_and_escaped_control_cross_all_boundaries(self):
        for text in ('A'*65536,'\U0001f642'*16384,'\x01'*65536):
            with self.subTest(kind=ord(text[0])):
                self.assertEqual(len(text.encode('utf-8')),65536)
                args={'path':'project/main.py','text':text,'expected_sha256':'NEW'};raw=json.dumps(args)
                self.assertLessEqual(len(raw),400000)
                response=make_codec().decode_completed(completed(raw))
                encoded=protocol.encode({'type':'response','data':response});self.assertLessEqual(len(encoded),protocol.MAX_FRAME)
                decoded=protocol.decode(encoded)['data'];protocol.validate_response(decoded,expected_model=MODEL)
                batch=protocol.tool_batch(decoded['choices'][0]['message']['tool_calls']);self.assertEqual(batch[0]['args'],args)
                g,r=gate();self.assertTrue(r.workspace_write_feedback_contract(ALIAS));g.validate_batch(batch,{ALIAS:r.exposed_schema('workspace','write_workspace_file')})
                parts,stored=workspace_module.validate_write_parameters(**{'path':args['path'],'text':text,'expected_sha256':'NEW'})
                self.assertEqual(parts,['project','main.py']);self.assertEqual(stored,text.encode('utf-8'));self.assertEqual(r.calls,0)

    def test_other_aliases_do_not_receive_large_parse_allowance(self):
        raw=json.dumps({'path':'main.py','text':'x'*13000})
        for name in ('other__write_workspace_file','workspace__run_workspace_command','write_workspace_file'):
            with self.subTest(name=name):
                with self.assertRaises(codec_module.ResponsesError):make_codec(name).decode_completed(completed(raw,name))
                with self.assertRaises(protocol.CandidateError):protocol.tool_batch([native(raw,name)])

    def test_gateway_requires_actual_discovered_workspace_permission_and_identity(self):
        args={'path':'main.py','text':'x'*13000};batch=[{'name':ALIAS,'args':args}]
        for field,value in (('grant',False),('module','other.server'),('identity','foreign'),('role','other'),('configured',False)):
            with self.subTest(field=field):
                g,r=gate()
                if field=='grant':r.session_grant['workspace_access']=value
                elif field=='configured':r.sessions['workspace'][1]['workspace_tools']=[]
                else:r.sessions['workspace'][1][field]=value
                self.assertFalse(r.workspace_write_feedback_contract(ALIAS))
                with self.assertRaises(gateway_module.CandidateError):g.validate_batch(batch,{ALIAS:SCHEMA})
                self.assertEqual(r.calls,0)

    def test_unknown_fields_and_host_identity_injection_still_fail_schema(self):
        for extra in ('unknown','generation','request_id','host_owner'):
            g,r=gate();args={'path':'main.py','text':'x'*13000,extra:'injected'}
            with self.assertRaises(ValidationError):g.validate_batch([{'name':ALIAS,'args':args}],{ALIAS:r.exposed_schema('workspace','write_workspace_file')})
            self.assertEqual(r.calls,0)

    def test_whole_IPC_frame_and_three_call_batch_limits_are_not_enlarged(self):
        raw=json.dumps({'path':'main.py','text':'\x01'*65536})
        calls=[native(raw),{**native(raw),'id':'call233'}]
        self.assertEqual(len(protocol.tool_batch(calls)),2)
        with self.assertRaisesRegex(protocol.CandidateError,'ipc_frame_size'):protocol.encode({'tool_calls':calls})
        with self.assertRaises(protocol.CandidateError):protocol.tool_batch([{**native('{}'),'id':'call'+str(i)} for i in range(4)])
        self.assertEqual(protocol.MAX_FRAME,524288);self.assertEqual(protocol.MAX_NATIVE_TOOLS,80)

    def test_400000_envelope_limit_and_original_response_bound_remain(self):
        too_large=json.dumps({'path':'main.py','text':'x'*400000})
        with self.assertRaises(codec_module.ResponsesError):make_codec().decode_completed(completed(too_large))
        with self.assertRaises(protocol.CandidateError):protocol.tool_batch([native(too_large)])
        g,r=gate()
        with self.assertRaises(gateway_module.CandidateError):g.validate_batch([{'name':ALIAS,'args':{'path':'main.py','text':'x'*400000}}],{ALIAS:SCHEMA})
        reply={'model':MODEL,'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':'x'*40001}}]}
        with self.assertRaises(protocol.CandidateError):protocol.validate_response(reply,expected_model=MODEL)
        self.assertEqual(r.calls,0)

    def test_large_completed_call_replays_exact_arguments_and_call_identity(self):
        raw=json.dumps({'path':'web/app.js','text':('literal\\n \"quote\" \U0001f642\n')*1800})
        c=make_codec();answer=c.decode_completed(completed(raw));message=answer['choices'][0]['message']
        p=payload();p['messages'] += [message,{'role':'tool','tool_call_id':'call232','name':ALIAS,'content':'{"ok":true,"verified_by_fresh_read":true}'}]
        wire=c.encode_request(p);calls=[x for x in wire['input'] if x.get('type')=='function_call']
        self.assertEqual(len(calls),1);self.assertEqual(calls[0]['arguments'],raw);self.assertEqual(calls[0]['call_id'],'call232');self.assertEqual(calls[0]['name'],ALIAS)
        self.assertEqual(json.loads(raw)['text'],json.loads(calls[0]['arguments'])['text'])

    def test_superseded_generation_cancels_before_dispatch(self):
        g,r=gate();g.current=lambda generation:False
        with self.assertRaises(asyncio.CancelledError):drive(g.execute(ALIAS,{'path':'main.py','text':'x'*65536},1))
        self.assertEqual(r.calls,0)

    def test_strict_JSON_names_and_unadvertised_calls_remain_rejected(self):
        for raw in ('{"path":"a.py","text":"x","text":"y"}','{"path":"a.py","text":NaN}'):
            with self.assertRaises(codec_module.ResponsesError):make_codec().decode_completed(completed(raw))
            with self.assertRaises(protocol.CandidateError):protocol.tool_batch([native(raw)])
        with self.assertRaises(codec_module.ResponsesError):make_codec().decode_completed(completed('{}','unknown_tool'))
        with self.assertRaises(protocol.CandidateError):protocol.tool_batch([native('{}','x'*65)])

    def test_backend_UTF8_limit_remains_65536_without_any_filesystem_call(self):
        self.assertEqual(workspace_module.MAX_WRITE,65536);self.assertEqual(workspace_module.MAX_FILE,65536);self.assertEqual(workspace_module.MAX_TOTAL,1048576)
        for text in ('x'*65537,'\U0001f642'*16385):
            with self.assertRaises(workspace_module.WriteParameterError) as caught:workspace_module.validate_write_parameters('main.py',text)
            self.assertEqual(caught.exception.reason,'workspace_write_size_or_encoding')

if __name__=='__main__':unittest.main(verbosity=2)
