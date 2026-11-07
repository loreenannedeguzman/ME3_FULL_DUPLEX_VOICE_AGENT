"""Execute the pinned upstream repetition guard without providers or tools."""
import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import tarfile
import unittest

SOURCE=Path(__file__).resolve().parents[1]
ROOT=Path(os.environ.get('ME3_PROJECT_ROOT','/opt/ME3Deployment'))
PIN='4a52ec2eb98fda8f05ab6d2e6b4c3895c358bc5a0fb75190efd4b8fd7a36ce59'
ARCHIVE_PIN='00da74309accbdf6f5483a9167d3b52da8f1a61826b7272d55f91babf08da346'


def pinned_source():
    configured=os.environ.get('ME3_PINNED_CHAT')
    runtime=ROOT/'.tmp/full-onit-trial-20261026-001'
    if configured:
        path=Path(configured)
    elif (runtime/'source/src/model/serving/chat.py').is_file():
        path=runtime/'source/src/model/serving/chat.py'
    else:
        # Local review copy is independently compared to the actual archive.
        path=SOURCE.parents[1]/'wb06-followup-repair-20261030-141/pinned-upstream-chat.py'
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=PIN:
        raise AssertionError('exact_pinned_native_chat_required')
    archive=runtime/'source.tar.gz'
    if not archive.is_file():
        archive=SOURCE.parents[1]/'onit-engine-comparison-20261026-001/onit-pinned.tar.gz'
    if hashlib.sha256(archive.read_bytes()).hexdigest()!=ARCHIVE_PIN:
        raise AssertionError('exact_Engine_ARCHIVE_SHA_required')
    with tarfile.open(archive) as source:
        member=source.getmember('sibyl-oracles-onit-e0bbe91/src/model/serving/chat.py')
        if not member.isfile() or source.extractfile(member).read()!=raw:
            raise AssertionError('imported_chat_differs_from_verified_archive_member')
    engine=ast.parse((ROOT/'onit_candidate/engine.py').read_bytes())
    archive_pin=next(ast.literal_eval(n.value) for n in engine.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='ARCHIVE_SHA' for t in n.targets))
    if archive_pin!=ARCHIVE_PIN:
        raise AssertionError('runtime_archive_authority_changed')
    return ast.parse(raw)


def guard_fixture():
    tree=pinned_source()
    execute=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='_execute_tool')
    start=next(i for i,n in enumerate(execute.body) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='call_key' for t in n.targets))
    # Compile the exact original terminal guard statements; no copied algorithm.
    shell=ast.parse('def guard(function_name,function_arguments,messages,tool_call_history,max_repeated,chat_ui=None,verbose=False):\n pass').body[0]
    shell.body=copy.deepcopy(execute.body[start:])
    refusal=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_policy_refusal_reason')
    constants=[copy.deepcopy(n) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id in {'_REPEAT_STEER_AFTER','_REPEAT_STREAK_BAIL','_REPEAT_REFUSAL_BAIL'} for t in n.targets)]
    ns={'json':json,'_log_to_ui_or_verbose':lambda *args,**kwargs:None}
    module=ast.fix_missing_locations(ast.Module(body=[*constants,copy.deepcopy(refusal),shell],type_ignores=[]))
    exec(compile(module,'<exact-pinned-repeat-guard>','exec'),ns)
    defaults=[n.value.args[1].value for n in ast.walk(tree) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='MAX_REPEATED_TOOL_CALLS' for t in n.targets)]
    if defaults!=[30]:raise AssertionError('pinned_repeat_default_changed')
    return ns['guard'],defaults[0]


class NativeLoop(unittest.TestCase):
    def test_changed_work_between_identical_audits_does_not_prematurely_stop(self):
        guard,limit=guard_fixture();history=[];messages=[]
        for revision in range(1,7):
            messages.append(dict(role='tool',content=json.dumps(dict(revision=revision,status='failed' if revision<6 else 'completed'))))
            self.assertIsNone(guard('workspace__run_workspace_command',dict(command_id='python_tests',path='test_acceptance.py'),messages,history,limit))
            if revision<6:
                messages.append(dict(role='tool',content=json.dumps(dict(revision=revision,write_verified=True))))
                self.assertIsNone(guard('workspace__write_workspace_file',dict(path='app.py',expected_sha256=str(revision)*64),messages,history,limit))
        self.assertEqual(len(history),11)

    def test_old_override_reproduces_fourth_audit_failure(self):
        guard,_=guard_fixture();history=[];messages=[];bail=None
        for revision in range(1,5):
            messages.append(dict(role='tool',content='verified diagnostic'))
            bail=guard('workspace__run_workspace_command',dict(command_id='python_tests',path='test_acceptance.py'),messages,history,2)
            if bail:break
            messages.append(dict(role='tool',content='verified changed source'))
            guard('workspace__write_workspace_file',dict(expected_sha256=str(revision)*64),messages,history,2)
        self.assertEqual(revision,4)
        self.assertIn('Stopped:',bail)

    def test_identical_consecutive_streak_still_stops_at_five(self):
        guard,limit=guard_fixture();history=[];messages=[]
        for repeat in range(1,6):
            messages.append(dict(role='tool',content='same verified result'))
            bail=guard('workspace__run_workspace_command',dict(command_id='python_tests',path='tests'),messages,history,limit)
            if repeat<5:self.assertIsNone(bail)
            else:self.assertIn('5 time(s) in a row',bail)

    def test_pinned_policy_refusal_still_stops_on_second_identical_call(self):
        guard,limit=guard_fixture();history=[];messages=[]
        for repeat in range(2):
            messages.append(dict(role='tool',content=json.dumps(dict(status='blocked',error='not authorized'))))
            bail=guard('effect',dict(value=1),messages,history,limit)
            if repeat==0:self.assertIsNone(bail)
            else:self.assertIn('refused by policy',bail)

    def test_child_uses_default_without_relaxing_other_loop_bounds(self):
        tree=ast.parse((SOURCE/'onit_candidate/child.py').read_bytes())
        call=next(n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='broker_chat' and any(k.arg=='max_chat_iterations' for k in n.keywords))
        kw={k.arg:k.value for k in call.keywords}
        self.assertNotIn('max_repeated_tool_calls',kw)
        self.assertEqual(ast.literal_eval(kw['max_api_retries']),1)
        for name in ('max_repeat_recoveries','max_repetition_continuations','max_nudge_decline_recoveries'):
            self.assertEqual(ast.literal_eval(kw[name]),0)
        self.assertEqual(ast.unparse(kw['max_chat_iterations']),"init.get('tool_rounds', 6) + MAX_FINAL_CONTINUATIONS")

    def test_actual_host_tool_stage_ceiling_requires_final_only_before_reservation(self):
        tree=ast.parse((ROOT/'onit_candidate/engine.py').read_bytes())
        branch=next(n for n in ast.walk(tree) if isinstance(n,ast.If) and ast.unparse(n.test)=="kind == 'model'")
        cap=next(n for n in ast.walk(branch) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='final_only' for t in n.targets))
        func=ast.parse('def check(calls,self,final_drafts):\n pass').body[0];func.body=[copy.deepcopy(cap),ast.Return(value=ast.Name(id='final_only',ctx=ast.Load()))]
        ns={};exec(compile(ast.fix_missing_locations(ast.Module(body=[func],type_ignores=[])),'<actual-host-tool-stage-boundary>','exec'),ns)
        class Registry:config={'runtime':{'tool_rounds':24}}
        class Owner:registry=Registry()
        self.assertFalse(ns['check'](23,Owner(),0));self.assertTrue(ns['check'](24,Owner(),0));self.assertTrue(ns['check'](1,Owner(),1))
        # The charged final request removes tool authority first. Actual Engine
        # flow and malicious25th-tool cases are exercised by test_openai_stage219.
        text=ast.unparse(branch)
        self.assertLess(text.index("payload.pop(name, None)"),text.index("self.budget.reserve('reasoning')"))
        self.assertIn("('tools', 'tool_choice', 'parallel_tool_calls')",text)
        self.assertIn('native_tool_after_stage_limit',text)
        self.assertIn('native_final_generation_limit',text)
        self.assertLess(text.index("self.budget.reserve('reasoning')"),text.index("self.transport.request('reasoning'"))
        self.assertEqual(json.loads((ROOT/'config/assistant.json').read_bytes())['runtime']['tool_rounds'],24)


if __name__=='__main__':unittest.main()
