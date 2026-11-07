"""Pure F1 wording and unchanged host-review boundary checks; no services/imports."""
import ast
import hashlib
import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import unittest

ROOT=Path(os.environ.get('ME3_PROJECT_ROOT','/opt/ME3Deployment'))
OVERLAY=Path(os.environ.get('ME3_F1_OVERLAY',Path(__file__).resolve().parents[1]))
EXPECTED={
 'onit_candidate/engine.py':(None,'6a2def4cb6a5c1c3bb9e69912835fdc3724b6de308cd90be1247500bea4dfc77'),
 'me3_productivity/action.py':('ProductivityControl','b0d6db31ebed13d19b9853ff0fdf2349c2201ea7eb7d51f42b97ab91278cdf81'),
 'me3_assistant/reminder_action.py':('ReminderControl','654ce163e4c53daddd23e11f9693e079b8761d7dd3e9aaef4bf81e80736da9f0'),
 'me3_assistant/actions.py':('ShoppingItem','464551b0f7b951a014be6db08bc6dece9c9377f5deca5997b4bc01b46b66184d'),
}

def tree(name):return ast.parse((OVERLAY/name).read_text(encoding='utf-8'))

def function(name,method,cls=None,extra=None):
    nodes=tree(name).body
    if cls:nodes=next(n for n in nodes if isinstance(n,ast.ClassDef) and n.name==cls).body
    node=next(n for n in nodes if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==method)
    namespace=dict(extra or {})
    exec(compile(ast.Module(body=[node],type_ignores=[]),'<actual-F1-method>','exec'),namespace)
    return namespace[method]

def finish(coro):
    # These actual host methods await only our immediate in-memory stub.
    try:coro.send(None)
    except StopIteration as result:return result.value
    finally:coro.close()
    raise AssertionError('Unexpected I/O suspension in pure fixture')

def prompt_delivered(history,prompt):
    # Exact original function is compiled without importing a service package.
    source=ast.parse((ROOT/'task_mcp/reasoner.py').read_text(encoding='utf-8'))
    node=next(n for n in source.body if isinstance(n,ast.FunctionDef) and n.name=='prompt_delivered')
    namespace={};exec(compile(ast.Module(body=[node],type_ignores=[]),'<host-delivery-check>','exec'),namespace)
    return namespace['prompt_delivered'](history,prompt)

class Presentation243(unittest.TestCase):
    def test_only_final_policy_and_three_success_methods_changed(self):
        for name,(cls,expected) in EXPECTED.items():
            with self.subTest(source=name):
                t=tree(name)
                if cls:
                    c=next(n for n in t.body if isinstance(n,ast.ClassDef) and n.name==cls)
                    c.body=[n for n in c.body if not isinstance(n,ast.FunctionDef) or n.name!='success_message']
                else:
                    t.body=[n for n in t.body if not (isinstance(n,ast.Assign) and any(isinstance(x,ast.Name) and x.id=='FINAL_POLICY' for x in n.targets) or isinstance(n,ast.AugAssign) and isinstance(n.target,ast.Name) and n.target.id=='FINAL_POLICY')]
                self.assertEqual(hashlib.sha256(ast.dump(t,include_attributes=False).encode()).hexdigest(),expected)

    def test_policy_keeps_precision_and_exact_reviews_not_a_redactor(self):
        text=' '.join(n.value.value for n in tree('onit_candidate/engine.py').body if (isinstance(n,ast.Assign) and any(isinstance(x,ast.Name) and x.id=='FINAL_POLICY' for x in n.targets) or isinstance(n,ast.AugAssign) and isinstance(n.target,ast.Name) and n.target.id=='FINAL_POLICY') and isinstance(n.value,ast.Constant))
        for phrase in ('lead with the useful answer or verified outcome','when requested or necessary','Preserve requested detail','Do not shorten, paraphrase or hide any field of a host-generated pending action review','never output tool/function/parameter markup','Never call model-rewritten text a verbatim reading'):
            self.assertIn(phrase,text)

    def test_productivity_all_outcomes_preserve_human_title_not_internal_ids(self):
        f=function('me3_productivity/action.py','success_message','ProductivityControl')
        for command in ('create','update','complete','reopen','cancel','archive'):
            p={'operation_id':'SECRET-OP','expected_digest':'SECRET-DIGEST','proposal':{'intent':{'kind':'note','command':command,'after':{'title':'API_docs résumé 42','id':'SECRET-RECORD','revision':99}}}}
            result=f(None,p)
            self.assertIn('API_docs résumé 42',result)
            self.assertNotIn('SECRET',result);self.assertNotIn('independently',result)
            if command=='complete':self.assertIn('as complete',result)

    def test_calendar_success_preserves_local_no_invitation_or_reminder_scope(self):
        f=function('me3_productivity/action.py','success_message','ProductivityControl')
        result=f(None,{'proposal':{'intent':{'kind':'event','command':'update','after':{'title':'Design review'}}}})
        self.assertIn('Design review',result);self.assertIn('local calendar',result)
        self.assertIn('no invitation or reminder was set',result)

    def test_reminder_preserves_exact_time_label_and_open_app_requirement(self):
        spoken=function('me3_assistant/reminder_action.py','spoken_time',extra={'datetime':datetime})
        f=function('me3_assistant/reminder_action.py','success_message','ReminderControl',{'spoken_time':spoken})
        result=f(None,{'proposal':{'intent':{'command':'schedule','kind':'reminder','label':'Review API_docs — batch 42','display_time':'2026-11-14T14:30:07+08:00'}}})
        for value in ('Review API_docs — batch 42','14 November 2026','2:30 PM and 7 seconds','UTC plus 8 hours','Keep ME3 open'):
            self.assertIn(value,result)
        self.assertNotIn('heard',result);self.assertNotIn('server',result)

    def test_reminder_cancel_does_not_claim_to_undo_old_alerts(self):
        f=function('me3_assistant/reminder_action.py','success_message','ReminderControl')
        result=f(None,{'proposal':{'intent':{'command':'cancel','kind':'alarm','label':'Tea'}}})
        self.assertIn('Tea',result);self.assertIn('does not undo any earlier alert',result)

    def test_shopping_preserves_exact_quantity_and_item(self):
        f=function('me3_assistant/actions.py','success_message','ShoppingItem')
        self.assertEqual(f(None,{'proposal':{'quantity':17,'item':'notebooks'}}),'Added 17 notebooks to your Pi shopping list.')

    def host(self):
        host=SimpleNamespace(pending={'prompt':'Send to test@example.invalid: Exact code API_docs 42. Confirm?','expires':200},clock=lambda:100,current=lambda g:g==7,events=SimpleNamespace(emit=lambda *a,**k:None),calls=[])
        host.cancel=lambda:setattr(host,'pending',None)
        async def execute(g,*,source):host.calls.append((g,source));host.pending=None;return 'VERIFIED fixture result'
        host.execute_pending=execute
        return host,function('me3_assistant/actions.py','confirm','Actions',{'prompt_delivered':prompt_delivered})

    def history(self,text,**extra):return [{'type':'model_output','content':[{'type':'text','text':text}],**extra},{'type':'user_input','content':[{'type':'text','text':'yes'}]}]

    def test_partial_or_paraphrased_review_cannot_authorize(self):
        for changed in ('partial','paraphrase','failed'):
            host,confirm=self.host();prompt=host.pending['prompt']
            history=self.history(prompt[:-8] if changed=='partial' else prompt.replace('42','43') if changed=='paraphrase' else prompt,**({'control_state':'failed'} if changed=='failed' else {}))
            self.assertEqual(finish(confirm(host,history,7)),prompt);self.assertEqual(host.calls,[])

    def test_exact_delivered_review_requires_current_separate_confirm_once(self):
        host,confirm=self.host();history=self.history(host.pending['prompt'])
        self.assertEqual(finish(confirm(host,history,7)),'VERIFIED fixture result')
        self.assertEqual(host.calls,[(7,'direct_current_user_after_completed_prompt')])
        self.assertIn('no pending action',finish(confirm(host,history,7)))
        self.assertEqual(len(host.calls),1)

    def test_stale_or_expired_confirmation_never_dispatches(self):
        host,confirm=self.host();history=self.history(host.pending['prompt'])
        self.assertEqual(finish(confirm(host,history,6)),'');self.assertEqual(host.calls,[])
        host.pending['expires']=50
        self.assertIn('expired',finish(confirm(host,history,7)));self.assertEqual(host.calls,[])

if __name__=='__main__':unittest.main(verbosity=2)
