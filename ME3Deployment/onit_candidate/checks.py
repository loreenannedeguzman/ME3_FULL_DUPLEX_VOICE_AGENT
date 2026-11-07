"""Focused candidate contracts. All providers/tools are fixtures; no live calls."""
import asyncio,copy,json,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from onit_candidate.protocol import MODEL,CandidateError,decode,encode,native_payload,validate_response
from onit_candidate.engine import Engine
from task_mcp.client import ROOT
DECL=dict(name='utilities__fixture_read',description='Fixture read only.',parameters={'type':'object','properties':{'value':{'type':'integer'}},'required':['value'],'additionalProperties':False})
def response(text='fixture complete',calls=None):
 return dict(id='fixture-provider-id',object='chat.completion',created=0,model=MODEL,service_tier='on_demand',choices=[dict(index=0,finish_reason='tool_calls' if calls else 'stop',message=dict(role='assistant',content=None if calls else text,**({'tool_calls':calls} if calls else {})))],usage=dict(prompt_tokens=10,completion_tokens=5,total_tokens=15))
def native(name=DECL['name'],args=None):return dict(id='fixture_call',type='function',function=dict(name=name,arguments=json.dumps(args or {'value':7})))
class ProtocolChecks(unittest.TestCase):
 def test_vendor_metadata_is_tolerant_but_response_semantics_are_validated(self):
  self.assertEqual(validate_response(response())['service_tier'],'on_demand')
  for modify in [lambda v:v.update(model='another-model'),lambda v:v['choices'][0].update(finish_reason='length'),lambda v:v['choices'][0]['message'].update(content=None)]:
   v=response();modify(v)
   with self.assertRaises(CandidateError):validate_response(v)
 def test_native_reply_accepts_null_content_and_rejects_duplicate_identity(self):
  validate_response(response(calls=[native()]))
  with self.assertRaises(CandidateError):validate_response(response(calls=[native(),native()]))
 def test_arguments_require_unique_finite_JSON_objects(self):
  for raw in ['{"a":1,"a":2}','{"a":NaN}','[1,2]']:
   call=native();call['function']['arguments']=raw
   with self.assertRaises(CandidateError):validate_response(response(calls=[call]))
 def test_IPC_is_bounded_and_preserves_unicode(self):
  self.assertEqual(decode(encode({'name':'고우림'})),{'name':'고우림'})
  with self.assertRaises(CandidateError):encode({'too_large':'x'*524288})
 def test_provider_payload_keeps_fixed_model_and_real_verdict_budget(self):
  p=native_payload(dict(model=MODEL,messages=[dict(role='user',content='fixture')],max_tokens=256,extra_body={'chat_template_kwargs':{'enable_thinking':False}},temperature=.9))
  self.assertEqual(p['max_completion_tokens'],256);self.assertEqual(p['temperature'],0);self.assertEqual(p['reasoning_effort'],'none');self.assertNotIn('extra_body',p)
  with self.assertRaises(CandidateError):native_payload(dict(model='fallback',messages=p['messages']))
 def test_whole_batch_validation_precedes_any_dispatch(self):
  r=FixtureRegistry();e=Engine(ROOT,r,None,Budget(),Events());schemas={DECL['name']:DECL['parameters']}
  with self.assertRaises(CandidateError):e.validate_batch([dict(name=DECL['name'],args={'value':7}),dict(name='commit_item',args={})],schemas)
  self.assertEqual(r.calls,0)
 def test_private_identity_and_invalid_types_are_rejected(self):
  r=FixtureRegistry();e=Engine(ROOT,r,None,Budget(),Events())
  for args in [{'value':True},{'value':7,'generation':1}]:
   with self.assertRaises(Exception):e.validate_batch([dict(name=DECL['name'],args=args)],{DECL['name']:DECL['parameters']})
 def test_broker_rejects_substituted_plan_arguments_and_unsolicited_batches(self):
  expected=[dict(id='actual_model_call',name=DECL['name'],args={'value':7})]
  Engine.validate_model_batch(copy.deepcopy(expected),expected)
  for changed in [[],[dict(id='actual_model_call',name=DECL['name'],args={'value':8})],[dict(id='actual_model_call',name='commit_item',args={})]]:
   with self.assertRaises(CandidateError):Engine.validate_model_batch(changed,expected)
  with self.assertRaises(CandidateError):Engine.validate_model_batch(expected,[])
class Budget:
 def __init__(self):self.used=0
 def permitted(self,role):return True
 def reserve(self,role):assert role=='reasoning';self.used+=1;return self.used
class Events:
 def __init__(self):self.rows=[]
 def emit(self,event,**kw):self.rows.append(dict(event=event,**kw))
class FixtureRegistry:
 def __init__(self,fail=False):self.config={'runtime':{'batch_calls':3,'tool_rounds':6}};self.reads={DECL['name']:None};self.workspace_operations=set();self.calls=0;self.fail=fail
 def declarations(self):return [copy.deepcopy(DECL)]
 async def read(self,name,g,args):
  self.calls+=1
  if self.fail:raise ValueError('intentional fixture tool failure')
  return dict(value=args['value'],read_only=True)
class FixtureTransport:
 def __init__(self,*rows,block=False):self.rows=list(rows);self.calls=0;self.entered=asyncio.Event();self.block=block
 async def request(self,*args,**kw):
  self.calls+=1;self.entered.set()
  if self.block:await asyncio.Event().wait()
  return self.rows.pop(0)
@unittest.skipUnless(sys.platform=='linux','Actual networkless Onit child requires Pi/Linux Bubblewrap and isolated dependencies.')
class OriginalOnitChecks(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='onit-adapter-fixture-');self.folder=Path(self.tmp.name)
 async def asyncTearDown(self):self.tmp.cleanup()
 async def check_engine(self,registry,transport):
  b=Budget();ev=Events();e=Engine(ROOT,registry,transport,b,ev)
  result=await e.answer('Read the fixture tool.',self.folder/'work',self.folder/'evidence')
  return result,e,b,ev
 async def test_original_loop_uses_native_tool_then_complete_final_with_Groq_metadata(self):
  r=FixtureRegistry();t=FixtureTransport(response(calls=[native()]),response('The fixture result is seven.'))
  result,e,b,ev=await self.check_engine(r,t)
  self.assertEqual(result['answer'],'The fixture result is seven.');self.assertEqual((r.calls,b.used,t.calls),(1,2,2));self.assertIsNone(e.task);self.assertTrue(ev.rows[-1]['child_closed'])
 async def test_unknown_later_tool_prevents_every_earlier_read(self):
  bad=native('not_permitted');bad['id']='fixture_bad';r=FixtureRegistry();t=FixtureTransport(response(calls=[native(),bad]));b=Budget();e=Engine(ROOT,r,t,b,Events())
  with self.assertRaises(CandidateError):await e.answer('Read fixtures.',self.folder/'work',self.folder/'evidence')
  self.assertEqual((r.calls,b.used,t.calls),(0,1,1));self.assertIsNone(e.task)
 async def test_first_MCP_failure_stops_the_original_loop_without_a_second_model_call(self):
  r=FixtureRegistry(fail=True);t=FixtureTransport(response(calls=[native()]));e=Engine(ROOT,r,t,Budget(),Events())
  with self.assertRaises(ValueError):await e.answer('Read fixture.',self.folder/'work',self.folder/'evidence')
  self.assertEqual(t.calls,1);self.assertIsNone(e.task)
 async def test_invalidation_cancels_pending_model_and_closes_owned_child_without_refund(self):
  r=FixtureRegistry();t=FixtureTransport(block=True);b=Budget();ev=Events();e=Engine(ROOT,r,t,b,ev)
  task=asyncio.create_task(e.answer('Read fixture.',self.folder/'work',self.folder/'evidence'));await asyncio.wait_for(t.entered.wait(),15);e.invalidate()
  with self.assertRaises(asyncio.CancelledError):await task
  self.assertEqual((b.used,t.calls,r.calls),(1,1,0));self.assertIsNone(e.task);self.assertTrue(ev.rows[-1]['child_closed'])
 async def test_new_task_cannot_start_before_cancel_cleanup_settles(self):
  r=FixtureRegistry();t=FixtureTransport(block=True);e=Engine(ROOT,r,t,Budget(),Events());entered=asyncio.Event();release=asyncio.Event();original=asyncio.wait_for
  async def delayed(awaitable,timeout):
   if timeout==2:entered.set();await release.wait()
   return await original(awaitable,timeout)
  with patch('onit_candidate.engine.asyncio.wait_for',delayed):
   task=asyncio.create_task(e.answer('Read fixture.',self.folder/'work',self.folder/'evidence'));await original(t.entered.wait(),15);e.invalidate()
   try:
    await original(entered.wait(),5)
    with self.assertRaisesRegex(CandidateError,'candidate_task_active'):await e.answer('New goal.',self.folder/'second-work',self.folder/'second-evidence')
    self.assertIsNotNone(e.task)
   finally:release.set()
   with self.assertRaises(asyncio.CancelledError):await task
  self.assertIsNone(e.task);self.assertFalse((self.folder/'second-work').exists())
if __name__=='__main__':unittest.main(verbosity=2)
