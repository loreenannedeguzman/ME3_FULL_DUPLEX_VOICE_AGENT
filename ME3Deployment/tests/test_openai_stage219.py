"""Actual Engine IPC/budget flow, scripted child/provider only; no execution."""
import asyncio,copy,json,os,tempfile,types,unittest
from pathlib import Path
from unittest.mock import AsyncMock,patch
from onit_candidate import engine,reasoning_profile
from onit_candidate.protocol import CandidateError,MAX_FINAL_DRAFTS,encode
ROOT=Path(os.environ['ME3_PROJECT_ROOT'])
DECL=dict(name='utilities__fixture_read',description='Synthetic bounded read.',parameters=dict(type='object',properties=dict(value=dict(type='integer')),required=['value'],additionalProperties=False))
TEXT='The verified fixture reads completed. Their results are available for review.'
def response(i,tool=False,length=False):
 message=dict(role='assistant',content=None if tool else TEXT)
 if tool:message['tool_calls']=[dict(id='call'+str(i),type='function',function=dict(name=DECL['name'],arguments=json.dumps(dict(value=i))))]
 return dict(id='response'+str(i),object='chat.completion',created=0,model=reasoning_profile.GEMINI_MODEL,choices=[dict(index=0,finish_reason='tool_calls' if tool else 'length' if length else 'stop',message=message)],usage=dict(prompt_tokens=10,completion_tokens=5,total_tokens=15))
class Registry:
 def __init__(self):self.config=dict(runtime=dict(tool_rounds=24,batch_calls=3));self.reads={DECL['name']:None};self.workspace_operations=set();self.calls=[]
 def declarations(self):return [copy.deepcopy(DECL)]
 async def read(self,name,g,args):self.calls.append(copy.deepcopy(args));return dict(value=args['value'],read_only=True)
class Budget:
 def __init__(self):self.used=0
 def permitted(self,role):return True
 def reserve(self,role):assert role=='reasoning';self.used+=1;return self.used
class Events:
 def __init__(self):self.rows=[]
 def emit(self,name,**data):self.rows.append((name,data))
class Transport:
 provider='google';model=reasoning_profile.GEMINI_MODEL
 def __init__(self,values):self.values=list(values);self.payloads=[];self.admission=types.SimpleNamespace(cap=5)
 async def request(self,role,**kw):self.payloads.append(copy.deepcopy(kw['json']));return self.values.pop(0)
class Pipe:
 def __init__(self,rows=()):self.rows=list(rows);self.writes=[]
 def write(self,raw):self.writes.append(raw)
 async def drain(self):pass
 async def readline(self):return self.rows.pop(0) if self.rows else b''
 async def read(self,count):return b''
 def close(self):pass
 async def wait_closed(self):pass
class Process:
 def __init__(self,rows):self.stdin=Pipe();self.stdout=Pipe(rows);self.returncode=0
 async def wait(self):return 0
def rows(extra_final=1,done=True):
 values=[];seq=0
 def add(kind,**data):
  nonlocal seq
  seq+=1;values.append(encode(dict(v=1,sequence=seq,kind=kind,**data)))
 payload=dict(model=reasoning_profile.GEMINI_MODEL,messages=[dict(role='user',content='Use the synthetic read tools, then explain the verified results.')],tools=[dict(type='function',function=DECL)],tool_choice='auto')
 for i in range(24):
  add('model',payload=copy.deepcopy(payload));add('batch',calls=[dict(id='call'+str(i),name=DECL['name'],args=dict(value=i))]);add('read',name=DECL['name'],args=dict(value=i))
 for i in range(extra_final):add('model',payload=copy.deepcopy(payload) if i==0 else {k:v for k,v in payload.items() if k not in {'tools','tool_choice'}})
 if done:add('done',answer=TEXT,finalization=dict(drafts=1,complete=True,native_answered=True,relationship='unchanged'))
 return values
def drive(coro):
 try:coro.send(None)
 except StopIteration as done:return done.value
 raise AssertionError('unexpected_IO_or_scheduler_yield')
class Timeout:
 async def __aenter__(self):return self
 async def __aexit__(self,*args):return False
async def immediate(awaitable,timeout):return await awaitable
class FinalStageChecks(unittest.TestCase):
 async def run_engine(self,values,ipc):
  registry=Registry();budget=Budget();transport=Transport(values);events=Events();process=Process(ipc)
  with tempfile.TemporaryDirectory(dir=ROOT,prefix='stage219-') as folder,patch.object(reasoning_profile,'ACTIVE','google_gemini'),patch.object(engine.asyncio,'current_task',return_value=object()),patch.object(engine.asyncio,'timeout',return_value=Timeout()),patch.object(engine.asyncio,'wait_for',immediate),patch.object(engine,'runtime',return_value=['synthetic-child']),patch.object(engine.asyncio,'create_subprocess_exec',AsyncMock(return_value=process)),patch('onit_candidate.grounding.task_context',return_value={}),patch('onit_candidate.grounding.context_messages',side_effect=lambda messages,ground:messages):
   candidate=engine.Engine(ROOT,registry,transport,budget,events)
   try:result=await candidate.answer('Read the fixture and report verified results.',Path(folder)/'work',Path(folder)/'evidence');return result,registry,budget,transport,candidate
   except BaseException as exc:return exc,registry,budget,transport,candidate
 def test_24_tool_stages_allow_one_charged_final_only_answer(self):
  result,registry,budget,tr,candidate=drive(self.run_engine([response(i,True) for i in range(24)]+[response(24)],rows()))
  self.assertIsInstance(result,dict);self.assertEqual(result['answer'],TEXT);self.assertEqual((len(registry.calls),budget.used,len(tr.payloads)),(24,25,25));self.assertIsNone(candidate.task)
  self.assertTrue(tr.payloads[23]['tools'])
  for key in ('tools','tool_choice','parallel_tool_calls'):self.assertNotIn(key,tr.payloads[24])
  self.assertIn('tool-stage budget is exhausted',tr.payloads[24]['messages'][-1]['content'])
 def test_25th_tool_call_is_rejected_before_any_extra_dispatch(self):
  result,registry,budget,tr,candidate=drive(self.run_engine([response(i,True) for i in range(25)],rows()))
  self.assertIsInstance(result,CandidateError);self.assertEqual(str(result),'native_tool_after_stage_limit');self.assertEqual((len(registry.calls),budget.used,len(tr.payloads)),(24,25,25));self.assertIsNone(candidate.task)
 def test_existing_final_draft_limit_is_not_extended(self):
  values=[response(i,True) for i in range(24)]+[response(24+i,length=True) for i in range(MAX_FINAL_DRAFTS)]
  result,registry,budget,tr,candidate=drive(self.run_engine(values,rows(MAX_FINAL_DRAFTS+1,False)))
  self.assertIsInstance(result,CandidateError);self.assertEqual(str(result),'native_final_generation_limit');self.assertEqual((len(registry.calls),budget.used,len(tr.payloads)),(24,24+MAX_FINAL_DRAFTS,24+MAX_FINAL_DRAFTS));self.assertIsNone(candidate.task)
if __name__=='__main__':unittest.main(verbosity=2)
