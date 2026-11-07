import asyncio,tempfile,threading,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
from me3_assistant.typed_music import TypedMusicOutput,Speaker

class OutputTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'bus';self.rows=[];self.events=SimpleNamespace(emit=lambda *a,**k:self.rows.append((a,k)));self.resources=[]
 async def asyncTearDown(self):self.tmp.cleanup()
 def opener(self,bus,fault):
  bus.mix(bytes(4));resource=SimpleNamespace(closed=False)
  def close():resource.closed=True
  resource.close=close;self.resources.append(resource);return resource
 async def test_metadata_session_creates_only_owned_bus_without_audio(self):
  open=AsyncMock();output=TypedMusicOutput(self.path,self.events,opener=open);self.assertTrue(self.path.exists());open.assert_not_called();await output.aclose();self.assertFalse(self.path.exists())
 async def test_open_is_lazy_once_reused_and_closed_after_owner(self):
  output=TypedMusicOutput(self.path,self.events,opener=self.opener);await output.ensure_open();await output.ensure_open();self.assertEqual(len(self.resources),1);self.assertFalse(self.resources[0].closed);await output.aclose();self.assertTrue(self.resources[0].closed)
 async def test_canceled_request_late_open_is_owned_and_closed_without_orphan(self):
  entered=threading.Event();release=threading.Event()
  def delayed(bus,fault):entered.set();release.wait(2);return self.opener(bus,fault)
  output=TypedMusicOutput(self.path,self.events,opener=delayed);request=asyncio.create_task(output.ensure_open())
  await asyncio.wait_for(asyncio.to_thread(entered.wait,2),3);request.cancel();await asyncio.gather(request,return_exceptions=True);closing=asyncio.create_task(output.aclose());await asyncio.sleep(0);release.set();await closing
  self.assertTrue(self.resources[0].closed);self.assertFalse(self.path.exists())
 async def test_failed_output_is_not_reopened_automatically(self):
  attempts=[]
  def failed(bus,fault):attempts.append(1);raise OSError('fixture')
  output=TypedMusicOutput(self.path,self.events,opener=failed)
  with self.assertRaises(OSError):await output.ensure_open()
  with self.assertRaises(ValueError):await output.ensure_open()
  self.assertEqual(len(attempts),1);await output.aclose()
 async def test_fault_latches_and_prevents_next_prepare(self):
  output=TypedMusicOutput(self.path,self.events,opener=self.opener);await output.ensure_open();output.fault.set()
  with self.assertRaises(ValueError):await output.ensure_open()
  await output.aclose()

class SpeakerTests(unittest.TestCase):
 def test_real_speaker_configuration_has_output_only_and_callback_fault_stops(self):
  import sys
  calls=[];bus=SimpleNamespace(mix=lambda pcm:b'\x01'*len(pcm));fault=threading.Event();stream=SimpleNamespace(stop_stream=lambda:calls.append('stop'),close=lambda:calls.append('close'))
  class Audio:
   def open(self,**kw):calls.append(kw);return stream
   def terminate(self):calls.append('terminate')
  module=SimpleNamespace(PyAudio=Audio,paInt16=8,paContinue=0,paAbort=2)
  def configure(settings):settings.output_rate=48000;settings.output_channels=2;settings.output_device=5
  with patch('platform.system',return_value='Linux'),patch('platform.machine',return_value='aarch64'),patch('voice_agent.configure_pi_audio',side_effect=configure),patch.dict(sys.modules,pyaudio=module):
   speaker=Speaker(bus,fault);args=calls[0];self.assertTrue(args['output']);self.assertFalse(args['input']);self.assertEqual(args['frames_per_buffer'],2880);self.assertEqual(args['stream_callback'](None,3,None,0),(b'\x01'*12,0));self.assertEqual(args['stream_callback'](None,3,None,4),(bytes(12),2));self.assertTrue(fault.is_set());speaker.close();self.assertEqual(calls[1:],['stop','close','terminate'])

class ActualMCPTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  from tests import test_assistant_music as music
  await music.MusicMCPTests.asyncSetUp(self)
 async def asyncTearDown(self):
  from tests import test_assistant_music as music
  await music.MusicMCPTests.asyncTearDown(self)
 def setup(self,r):
  from tests import test_assistant_standing_music as standing
  return standing.ActualMCPTests.setup(self,r)
 async def test_obsolete_request_after_lazy_output_never_prepares_or_spends(self):
  from me3_assistant.registry import Registry
  from me3_assistant.actions import Actions
  async with Registry(self.events,self.cfg,music_bus=self.bus.path) as r:
   self.setup(r);current=True
   async def open():
    nonlocal current
    current=False
   r.ensure_music_output=open;a=Actions(r,r.config['actions'],r.budget,r.journal,self.events,lambda g:current,allowed=r.allowed_actions)
   self.assertEqual(await a.propose('propose_music_control',{'command':'play','track_id':'Study.wav'},1),'');self.assertEqual(a.budget._used(),0);self.assertEqual(r.journal.records,{});self.assertEqual(r.calls,0)
 async def test_playlist_metadata_does_not_open_speaker(self):
  from me3_assistant.registry import Registry
  from me3_assistant.actions import Actions
  async with Registry(self.events,self.cfg,music_bus=self.bus.path) as r:
   self.setup(r);r.ensure_music_output=AsyncMock(side_effect=AssertionError('metadata must not open audio'));a=Actions(r,r.config['actions'],r.budget,r.journal,self.events,lambda g:True,allowed=r.allowed_actions)
   result=await a.propose('propose_music_control',{'command':'save_playlist','playlist_name':'Typed','track_ids':['Study.wav']},1);self.assertIn('Saved playlist',result);r.ensure_music_output.assert_not_called();self.assertEqual(a.budget._used(),1)
 async def test_real_decoder_and_MCP_use_lazy_owned_output_then_stop(self):
  import shutil,time
  if not shutil.which('ffmpeg'):self.skipTest('FFmpeg not installed on this host')
  from me3_assistant.registry import Registry
  from me3_assistant.actions import Actions
  resources=[]
  def opener(bus,fault):
   end=threading.Event()
   def consume():
    while not end.is_set():bus.mix(bytes(3840));time.sleep(.01)
   worker=threading.Thread(target=consume);worker.start()
   def close():end.set();worker.join(2);assert not worker.is_alive()
   resources.append(worker);return SimpleNamespace(close=close)
  output=TypedMusicOutput(self.root/'typed-bus',self.events,opener=opener)
  try:
   async with Registry(self.events,self.cfg,music_bus=output.bus.path) as r:
    self.setup(r);r.ensure_music_output=output.ensure_open;a=Actions(r,r.config['actions'],r.budget,r.journal,self.events,lambda g:True,allowed=r.allowed_actions)
    for args in [dict(command='play',track_id='Study.wav'),dict(command='pause'),dict(command='volume',volume=12),dict(command='resume'),dict(command='stop')]:
     result=await a.propose('propose_music_control',args,1);self.assertNotIn('could not',result);self.assertEqual(list(r.journal.records.values())[-1]['state'],'VERIFIED')
    self.assertEqual(a.budget._used(),5);self.assertEqual(len(resources),1)
  finally:await output.aclose()
  self.assertFalse(resources[0].is_alive());self.assertFalse(output.bus.path.exists())

class ConsoleOwnerTests(unittest.IsolatedAsyncioTestCase):
 async def check_owner(self,fail):
  from tests import test_assistant_mode as f
  from me3_assistant.gui import Console
  from me3_assistant.registry import Registry
  order=[];outputs=[]
  class TrackedRegistry(Registry):
   async def __aexit__(self,*args):
    result=await super().__aexit__(*args);order.append('MCP closed');return result
  class Output(TypedMusicOutput):
   def __init__(self,path,events):super().__init__(path,events);outputs.append(self)
   async def aclose(self):await super().aclose();order.append('output closed')
  class Model(f.Model):
   def __init__(self,events):super().__init__(f.decision(text='Ready.'))
   async def close(self):order.append('model closed')
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);cfg=f.config('extension');cfg['sha256']='fixture';c=Console(cfg,{'revision':'fixture'});c.mode='text';c.stop_event=asyncio.Event();c.ready=asyncio.Event()
   grant=dict(profile='standing',routine_local_music=True,allowed_actions=['propose_music_control'],workspace_access=False,operation_state_path=str(root/'state'))
   with patch('me3_assistant.gui.NORMAL',root),patch('me3_assistant.gui.SharedBudget',return_value=f.FixtureBudget(1)),patch('me3_assistant.registry.Registry',TrackedRegistry),patch('me3_assistant.typed_music.TypedMusicOutput',Output),patch('me3_assistant.reasoning.Reasoning',Model),patch('dotenv.load_dotenv'):
    c.task=asyncio.create_task(c.text(grant,{}))
    try:
     await asyncio.wait_for(c.ready.wait(),15);self.assertIsNotNone(c.router.tasks.ensure_music_output);self.assertIn('Local music',c.notice);self.assertIsNone(outputs[0].open_task)
     if fail:outputs[0].fault.set();await asyncio.wait_for(asyncio.shield(c.task),5);self.assertIn('music output stopped',c.public_error)
     else:await c.stop()
    finally:
     c.stop_event.set();await asyncio.wait_for(asyncio.shield(c.task),10)
   self.assertEqual(order,['MCP closed','output closed','model closed']);self.assertTrue(outputs[0].closed);self.assertEqual(c.state,'stopped')
 async def test_console_closes_MCP_before_unopened_output(self):await self.check_owner(False)
 async def test_first_callback_fault_stops_console_without_retry(self):await self.check_owner(True)
