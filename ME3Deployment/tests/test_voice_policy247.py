"""Provider-free voice-only policy plumbing; not a measured latency claim."""
import ast
import copy
import hashlib
import os
from pathlib import Path
import unittest

STAGE=Path(os.environ.get('ME3_LATENCY_STAGE',Path(__file__).resolve().parents[1]))
BASE_AST={
 'engine.py':'727c99770f30bdda88a7b97aaddc5356aea1e4fe7617e5751aacc349efaf68b5',
 'conversation.py':'3ea73ee118b2a98f9328e7369cbaaa419e1c8100f015b8cf99df3493096aa429',
}

def tree(name):return ast.parse((STAGE/'onit_candidate'/name).read_text(encoding='utf-8'))

def policies():
    namespace={'CandidateError':ValueError}
    nodes=[]
    for node in tree('engine.py').body:
        if isinstance(node,ast.Assign) and any(isinstance(x,ast.Name) and x.id in {'FINAL_POLICY','VOICE_FINAL_POLICY'} for x in node.targets):nodes.append(node)
        elif isinstance(node,ast.AugAssign) and isinstance(node.target,ast.Name) and node.target.id=='FINAL_POLICY':nodes.append(node)
        elif isinstance(node,ast.FunctionDef) and node.name=='final_policy':nodes.append(node)
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'<actual-voice-policy>','exec'),namespace)
    return namespace

class RestoreBaseline(ast.NodeTransformer):
    def __init__(self,engine):self.engine=engine
    def visit_Module(self,node):
        if self.engine:
            node.body=[n for n in node.body if not (isinstance(n,ast.Assign) and any(isinstance(x,ast.Name) and x.id=='VOICE_FINAL_POLICY' for x in n.targets) or isinstance(n,ast.FunctionDef) and n.name=='final_policy')]
        return self.generic_visit(node)
    def visit_AsyncFunctionDef(self,node):
        if self.engine and node.name=='answer':
            indexes=[i for i,x in enumerate(node.args.kwonlyargs) if x.arg=='spoken_response']
            assert len(indexes)==1
            i=indexes[0];del node.args.kwonlyargs[i];del node.args.kw_defaults[i]
            assert isinstance(node.body[0],ast.If) and isinstance(node.body[0].body[0],ast.Raise)
            assert node.body[0].body[0].exc.args[0].value=='spoken_response_mode_must_be_boolean'
            del node.body[0]
        return self.generic_visit(node)
    def visit_Call(self,node):
        if self.engine and isinstance(node.func,ast.Name) and node.func.id=='final_policy':
            assert len(node.args)==1 and isinstance(node.args[0],ast.Name) and node.args[0].id=='spoken_response'
            return ast.copy_location(ast.Name(id='FINAL_POLICY',ctx=ast.Load()),node)
        if not self.engine and isinstance(node.func,ast.Attribute) and node.func.attr=='answer':
            node.keywords=[k for k in node.keywords if k.arg!='spoken_response']
        return self.generic_visit(node)

class VoicePolicy247(unittest.TestCase):
    def test_text_policy_is_byte_identical_default_and_explicit_false(self):
        p=policies()
        self.assertEqual(p['final_policy'](),p['FINAL_POLICY'])
        self.assertEqual(p['final_policy'](False),p['FINAL_POLICY'])

    def test_voice_keeps_complete_existing_policy_and_detail_exceptions(self):
        p=policies();value=p['final_policy'](True)
        self.assertTrue(value.startswith(p['FINAL_POLICY']))
        self.assertEqual(value,p['FINAL_POLICY']+p['VOICE_FINAL_POLICY'])
        for phrase in ('One sentence is enough','usually no more than 70 words','There is no minimum length','For a simple how-to question','Omit optional background, multiple alternatives','starting preference, not a hard cap','long explanations, exact readings, code and multi-part requests','Never silently omit a requested part','all exact host action reviews','source attribution','same permitted tools and verification rules'):
            self.assertIn(phrase,value)

    def test_no_length_enforcement_or_minimum(self):
        value=policies()['VOICE_FINAL_POLICY']
        self.assertNotIn('60 to 100',value)
        self.assertIn('There is no minimum length',value)
        self.assertIn('not a hard cap',value)

    def test_host_mode_requires_literal_boolean(self):
        for bad in (None,0,1,'voice','false',{},[]):
            with self.subTest(value=bad),self.assertRaisesRegex(ValueError,'spoken_response_mode_must_be_boolean'):
                policies()['final_policy'](bad)

    def test_voice_mode_comes_from_actual_conversation_not_words_or_tools(self):
        calls=[n for n in ast.walk(tree('conversation.py')) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and isinstance(n.func.value,ast.Attribute) and n.func.value.attr=='engine' and n.func.attr=='answer']
        self.assertEqual(len(calls),1)
        values=[k.value for k in calls[0].keywords if k.arg=='spoken_response']
        self.assertEqual(len(values),1)
        self.assertEqual(ast.dump(values[0]),"Attribute(value=Name(id='self', ctx=Load()), attr='voice', ctx=Load())")

    def test_engine_only_policy_plumbing_changes_guards_and_tools_unchanged(self):
        restored=RestoreBaseline(True).visit(copy.deepcopy(tree('engine.py')))
        self.assertEqual(hashlib.sha256(ast.dump(restored,include_attributes=False).encode()).hexdigest(),BASE_AST['engine.py'])

    def test_conversation_other_behavior_identical_pending_review_not_rewritten(self):
        restored=RestoreBaseline(False).visit(copy.deepcopy(tree('conversation.py')))
        self.assertEqual(hashlib.sha256(ast.dump(restored,include_attributes=False).encode()).hexdigest(),BASE_AST['conversation.py'])

    def test_stable_policy_no_request_metadata_cache_or_streaming_side_channel(self):
        p=policies();a=p['final_policy'](True)
        self.assertEqual(a,p['final_policy'](True));self.assertLess(len(p['VOICE_FINAL_POLICY']),2000)
        helper=next(n for n in tree('engine.py').body if isinstance(n,ast.FunctionDef) and n.name=='final_policy')
        self.assertFalse(any(isinstance(n,(ast.Await,ast.Yield,ast.YieldFrom)) for n in ast.walk(helper)))
        self.assertFalse((STAGE/'me3_openai').exists())
        self.assertFalse((STAGE/'onit_candidate/child.py').exists())

if __name__=='__main__':unittest.main(verbosity=2)
