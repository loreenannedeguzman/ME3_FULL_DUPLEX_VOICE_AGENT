"""Actual current normal settings/pipeline/BaseOutput and new observer/reset seam.

No VAD model, devices, inference, provider credentials or child processes.
Only VAD/endpointer/PCM/result inputs are authored; media sender is Pipecat's.
"""
import ast,asyncio,dataclasses,importlib.util,json,os,sys,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
P=Path(__file__).resolve().parent

class CurrentTransport(unittest.TestCase):
 def test_actual_current_pipeline_settings_sender_marks_and_end_fence(self):
  from voice_agent import Settings
  from duplex_audio import DuplexState
  from pipecat.audio.vad.vad_analyzer import VADState
  from pipecat.frames.frames import StartFrame,CancelFrame,OutputAudioRawFrame,LLMFullResponseStartFrame,LLMFullResponseEndFrame,TTSStoppedFrame
  from pipecat.processors.frame_processor import FrameDirection,FrameProcessorSetup
  from pipecat.clocks.system_clock import SystemClock
  from pipecat.utils.asyncio.task_manager import TaskManager
  from digital_transport import DigitalTransport
  from stage_observer import StageObserver
  from test_changed441 import Events
  class VAD:
   def __init__(self,**kwargs):pass
   def set_sample_rate(self,rate):assert rate==16000
   async def analyze_audio(self,data):return VADState.QUIET
  class Endpoint:
   def __init__(self,events):pass
  class Wire:
   _uri='ws://127.0.0.1:8791?out_sr=48000'
   def __init__(self):self.refs=0;self.is_connected=False;self.sent=[]
   async def setup(self,manager):pass
   async def connect(self):self.refs+=1;self.is_connected=True
   async def disconnect(self):self.refs-=1;self.is_connected=self.refs>0
   async def send(self,data):self.sent.append(data);return True
  tree=ast.parse((P/'overlay/onit_candidate/voice.py').read_text())
  node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='normal_settings')
  scope=dict(os=os,replace=dataclasses.replace)
  exec(compile(ast.Module(body=[node],type_ignores=[]),'actual_current_normal_settings441','exec'),scope)
  with patch.dict(os.environ,{},clear=True),patch('me3_assistant.backends.check_ready',lambda backend:None):
   settings=scope['normal_settings']({'runtime':{'max_session_seconds':20}},'groq')
  self.assertEqual((settings.output_rate,settings.output_channels),(24000,1))
  import me3_completion.pipeline as parent
  name='me3_completion.pipeline';old=sys.modules[name];path=P/'overlay/me3_completion/pipeline.py'
  spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec)
  sys.modules[name]=module
  try:spec.loader.exec_module(module)
  finally:sys.modules[name]=old
  from onit_candidate.conversation import controller_class,Context
  module.ConversationController=controller_class()
  async def case():
   events=Events();events.input_frames=0;events.output_frames=0
   from pipecat_fdbench import make_benchmark_transport
   course=make_benchmark_transport('ws://127.0.0.1:18894/',in_sr=16000,out_sr=48000)
   self.assertIsNone(course._input);self.assertIsNone(course._output)
   self.assertIn('out_sr=48000',course._session._uri)
   self.assertTrue(all(callable(getattr(course._session,n)) for n in ('setup','connect','send','disconnect')))
   wire=Wire();course._session=wire # Actual upstream factory; authored no-socket wire.
   meta=dict(source_revision='f'*64,contract_sha256='a'*64)
   bridge=DigitalTransport(course,settings,events,ready_metadata=meta)
   speech=NS();provider=NS()
   with patch('pipecat.audio.vad.silero.SileroVADAnalyzer',VAD),patch.object(module,'LocalEndpoint',Endpoint),patch('duplex_audio.make_duplex_transport',side_effect=AssertionError('hardware_forbidden')):
    result=module.build_pipeline(settings,events,speech,provider,transport_factory=bridge.factory)
   processor=result[3];controller=processor.controller
   controller.shared_context=Context()
   self.assertIs(processor.output_transport,bridge.output())
   async def asr(role,generation,**arguments):return dict(text='Actual class authored ASR result.',ok=True,request_id='asr-fixture')
   controller.real_clients=NS(call=asr);controller.clock.advance();controller.slot=dict(generation=1,pcm=b'\0\0'*160,stage='asr',submitted=__import__('time').monotonic(),candidate=None)
   import tempfile
   with tempfile.TemporaryDirectory() as d:
    resource=NS(bridge=bridge,processor=processor,log_dir=d,document=meta,sha256='a'*64)
    stages=StageObserver(resource,events);bridge.stage_observer=stages
    clock=SystemClock();clock.start();manager=TaskManager()
    setup=FrameProcessorSetup(clock=clock,task_manager=manager,pipeline_worker=None,audio_in_sample_rate=16000,audio_out_sample_rate=24000)
    output=bridge.output();input_=bridge.input()
    async def sink(frame,*args):pass
    input_.push_frame=sink;output.push_frame=sink
    try:
     await asyncio.gather(input_.setup(setup),output.setup(setup))
     await input_.process_frame(StartFrame(),FrameDirection.DOWNSTREAM)
     await output.process_frame(StartFrame(),FrameDirection.DOWNSTREAM)
     bridge.ready.set();bridge.core.mark_listening_ready()
     await controller.clients.call('asr',1,pcm_b64='authored')
     events.emit('generation_playout_started',generation=1,kind='response')
     begin=LLMFullResponseStartFrame();begin.m3_generation=1
     await output.process_frame(begin,FrameDirection.DOWNSTREAM)
     await output.process_frame(OutputAudioRawFrame(audio=b'\x20\x03'*480,sample_rate=24000,num_channels=1),FrameDirection.DOWNSTREAM)
     await output.process_frame(TTSStoppedFrame(),FrameDirection.DOWNSTREAM)
     end=LLMFullResponseEndFrame();end.m3_generation=1
     await output.process_frame(end,FrameDirection.DOWNSTREAM)
     async with asyncio.timeout(3):
      while not any(r['event']=='digital_media_tail_drained' for r in events.rows):await asyncio.sleep(.005)
     stages.mark('llm_usage',prompt_tokens=123)
     await bridge.end_session(dict(event='me3_end_session',session=bridge.observation_session,**meta))
     parsed=[json.loads(v) for v in wire.sent if isinstance(v,str)]
     ack=next(i for i,v in enumerate(parsed) if v['event']=='me3_end_session_ack')
     self.assertTrue(any(v.get('stage')=='asr_final' for v in parsed[:ack]))
     self.assertTrue(any(v.get('stage')=='bot_stopped' for v in parsed[:ack]))
     self.assertEqual(parsed[ack-1]['stage'],'llm_usage')
     self.assertTrue(parsed[ack]['writes_fenced']);self.assertEqual(len(stages.queue),0)
     with self.assertRaises(ValueError):await bridge.core.send_control({'event':'mark','stage':'invalid'})
    finally:
     await input_.cancel(CancelFrame());await output.cancel(CancelFrame())
     await input_.cleanup();await output.cleanup();await bridge.close()
     stages.finish()
    self.assertTrue(bridge.cleanup_complete);self.assertEqual(wire.refs,0)
  from test_changed441 import run
  run(case(),15)
