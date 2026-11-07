"""Changed course/observer/resource seams only; no inference, scoring or effects."""
import ast,asyncio,hashlib,importlib,json,sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from stage_observer import StageObserver
from benchmark_resources import capacities,validate
P=Path(__file__).resolve().parent

class NoIOSelector:
 def get_map(self):return {}
 def select(self,timeout=None):
  if timeout:time.sleep(min(timeout,.01))
  return []
 def close(self):pass
 def register(self,*args):raise AssertionError('fixture_IO_forbidden')
 def unregister(self,*args):raise AssertionError('fixture_IO_forbidden')
class NoIOLoop(asyncio.SelectorEventLoop):
 def __init__(self):super().__init__(NoIOSelector())
 def _make_self_pipe(self):pass
 def _close_self_pipe(self):pass
 def _write_to_self(self):pass
def run(coro,seconds=15):
 with asyncio.Runner(loop_factory=NoIOLoop) as runner:return runner.run(asyncio.wait_for(coro,seconds))

class Events:
 def __init__(self):self.rows=[];self.observer=None
 def emit(self,event,**fields):
  self.rows.append(dict(event=event,**fields))
  if self.observer:self.observer(event,**fields)

def fixture(directory):
 events=Events();sent=[]
 async def control(value):sent.append(dict(value))
 async def pcm(data):sent.append(bytes(data))
 async def call(role,generation,**arguments):return dict(text='The exact authored transcript.',request_id='fixture-asr',ok=True)
 clients=NS(call=call);controller=NS(clients=clients,real_clients=clients,clock=NS(current=1),closed=False)
 ready=asyncio.Event();bridge=NS(core=NS(send_control=control,send_pcm=pcm),ready=ready,write_blocked=False)
 resource=NS(bridge=bridge,processor=NS(controller=controller),log_dir=directory,document={'source_revision':'f'*64},sha256='a'*64)
 return resource,events,sent

class Changed441(unittest.TestCase):
 def test_upstream_queue_stage_pairs_and_exact_allowance(self):
  from fdbench.queue import build_plan
  from fdbench.suites import GROUPS,ROUND_GROUPS
  class Dataset:
   def clips(self,g):
    if g=='stage_latency':return [NS(id='s'+str(i),meta={}) for i in range(10)]
    if g=='false_tt':return [NS(id='f'+str(i),meta={'kind':k}) for i,k in enumerate(GROUPS[g].kinds)]
    return [NS(id=g+'0',meta={})]
  plan=build_plan(Dataset(),1,000000000,phrases=10,runs=3)
  self.assertEqual(len(plan.items),40)
  self.assertTrue(all(i.group=='stage_latency' for i in plan.items[:30]))
  executions=sum(2 if GROUPS[i.group].paired else 1 for i in plan.items)
  self.assertEqual(executions,44)
  self.assertEqual(len({(i.round,i.group,i.clip_id) for i in plan.items}),40)
  cap=capacities(1,10,3)
  self.assertEqual((cap['queue_items'],cap['executions'],cap['local_scoring_transcriptions'],cap['judge_requests']),(40,44,52,5))
  self.assertEqual(cap['reservations'],dict(asr=704,reasoning=704,tts=2816,commit_item=0))
  for args in [(True,10,3),(1,0,3),(31,10,3),(0,0,0),(1,11,3)]:
   with self.assertRaises(ValueError):capacities(*args)
 def test_seam_reverses_to_exact_current_normal_AST_and_signatures(self):
  spec=json.loads((P/'voice-seam-diff.json').read_bytes());candidate=(P/'overlay/onit_candidate/voice.py').read_text()
  for row in reversed(spec['replacements']):
   self.assertEqual(candidate.count(row['after']),1);candidate=candidate.replace(row['after'],row['before'])
  self.assertEqual(ast.dump(ast.parse(candidate)),ast.dump(ast.parse((P/'baseline/onit_candidate/voice.py').read_text())))
  before=ast.parse((P/'baseline/me3_completion/pipeline.py').read_text())
  after=ast.parse((P/'overlay/me3_completion/pipeline.py').read_text())
  f=next(n for n in after.body if isinstance(n,ast.FunctionDef) and n.name=='build_pipeline')
  f.args.kwonlyargs=[];f.args.kw_defaults=[]
  statement=next(n for n in f.body if isinstance(n,ast.Assign) and isinstance(n.value,ast.Call) and isinstance(n.value.func,ast.IfExp))
  statement.value.func=ast.Name(id='make_duplex_transport',ctx=ast.Load())
  self.assertEqual(ast.dump(before),ast.dump(after))
 def test_actual_marks_store_and_stage_scorer_accept_exact_unchopped_times(self):
  from fdbench.marks import MarksStore
  from fdbench.score.stages import turn_stages
  async def case():
   with tempfile.TemporaryDirectory() as d:
    r,e,sent=fixture(d);captured_emit=e.emit;o=StageObserver(r,e);r.bridge.ready.set()
    e.emit('digital_vad_state',state='SPEAKING',generation=1,software_ns=1_000_000_000)
    e.emit('digital_vad_state',state='STOPPING',generation=1,software_ns=1_100_000_000)
    e.emit('digital_vad_state',state='QUIET',generation=1,software_ns=2_000_000_000)
    o.mark('asr_final',t=2.5,text='word '*180,generation=1)
    captured_emit('openai_plan_request_dispatch',ordinal=1,model='gpt-6.1-sol',software_ns=2_600_000_000)
    e.emit('openai_plan_stage_timing',ordinal=1,stage='decode_finished',monotonic_ns=2_700_000_000)
    self.assertFalse(any(m['stage']=='llm_first_token' for m in o.rows))
    e.emit('openai_plan_stage_timing',ordinal=1,stage='first_output_text_delta_received',monotonic_ns=3_000_000_000)
    e.emit('openai_plan_request_completed',ordinal=1,usage=dict(prompt_tokens=123,completion_tokens=9),software_ns=3_100_000_000)
    e.emit('mcp_request_start',role='tts',generation=1,request_id='t',software_ns=3_200_000_000)
    e.emit('mcp_request_end',role='tts',generation=1,request_id='t',result_bytes=960,software_ns=3_400_000_000)
    e.emit('generation_playout_started',kind='task_ack',generation=1,software_ns=3_450_000_000)
    self.assertFalse(any(m['stage']=='audio_out' for m in o.rows))
    e.emit('generation_playout_started',kind='response',generation=1,software_ns=3_500_000_000)
    await r.bridge.core.send_pcm(b'pcm');self.assertEqual(sent[0],b'pcm')
    store=MarksStore()
    for m in sent[1:]:store.handle_message(json.dumps(m))
    marks=[dict(m,tt=m['t']) for m in store.marks]
    result=turn_stages(marks,user_end=2.,bot_start=3.5,ref_text='word '*180)
    self.assertEqual((result['e2e'],result['asr_final'],result['llm_ttft'],result['tts_ttfb']),(1.5,.5,.5,.2))
    self.assertEqual(result['context_tokens'],123);self.assertEqual(result['wer']['wer'],0)
    self.assertGreater(len(result['transcript']),500);o.finish()
    self.assertTrue(json.loads((Path(d)/'stage-observation.json').read_bytes())['complete'])
  run(case())
 def test_real_ASR_return_stale_error_and_greeting_exclusion(self):
  async def case():
   with tempfile.TemporaryDirectory() as d:
    r,e,sent=fixture(d);o=StageObserver(r,e)
    e.emit('generation_playout_started',kind='response',generation=0)
    r.bridge.ready.set();await r.processor.controller.real_clients.call('asr',1,pcm_b64='authored')
    await r.processor.controller.real_clients.call('asr',0,pcm_b64='authored')
    self.assertEqual([m['stage'] for m in o.rows],['asr_final'])
    self.assertEqual(o.rows[0]['text'],'The exact authored transcript.')
    await o.flush();o.finish();self.assertEqual(len(e.rows),1)
  run(case())
 def test_bounded_observation_overflow_and_send_failure_not_hidden(self):
  async def case():
   with tempfile.TemporaryDirectory() as d:
    r,e,sent=fixture(d);o=StageObserver(r,e,maximum=1);o.mark('vad_end')
    with self.assertRaisesRegex(ValueError,'overflow'):o.mark('vad_end')
    with self.assertRaisesRegex(ValueError,'incomplete'):o.finish()
   with tempfile.TemporaryDirectory() as d:
    r,e,sent=fixture(d);o=StageObserver(r,e);o.mark('vad_end')
    async def failed(value):raise ValueError('authored_wire_failure')
    r.bridge.core.send_control=failed
    with self.assertRaisesRegex(ValueError,'authored_wire_failure'):await o.flush()
    self.assertEqual((o.sent,len(o.queue)),(0,1))
    with self.assertRaisesRegex(ValueError,'incomplete'):o.finish()
  run(case())

if __name__=='__main__':unittest.main()
