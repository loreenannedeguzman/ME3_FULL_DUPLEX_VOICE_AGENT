"""Provider-free framing/ownership/host-profile tests; no device opened."""
import asyncio,threading,unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from duplex_audio import DuplexState,CaptureAudio
from voice_agent import Settings
from me3_assistant.acoustic_echo import attach,EchoControl
class ProfileChecks(unittest.TestCase):
 def test_only_explicit_input_profile_changes_its_own_open_arguments(self):
  calls=[];events=[];state=DuplexState(Settings(api_key=''),NS(emit=lambda *a,**kw:events.append((a,kw))));audio=NS(open=lambda **kw:(calls.append(kw) or NS(get_input_latency=lambda:.16)));device=CaptureAudio(audio,state);args=dict(input=True,rate=16000,channels=1,frames_per_buffer=320)
  device.open(**args);self.assertEqual(calls[-1],args);state.configure_input_callback(4);device.open(**args);self.assertEqual(calls[-1]['frames_per_buffer'],1280);self.assertFalse(calls[-1]['start']);self.assertEqual(args['frames_per_buffer'],320);self.assertEqual(events[-1][1]['AEC_VAD_frame_ms'],20)
  with self.assertRaises(ValueError):device.open(**dict(args,output=True))
 def test_profile_is_bounded_and_cannot_change_after_callbacks_start(self):
  state=DuplexState(Settings(api_key=''),None)
  for value in [True,0,2,3,5]:
   with self.assertRaises(ValueError):state.configure_input_callback(value)
  state.configure_input_callback(4);state.capture_count=1
  with self.assertRaises(ValueError):state.configure_input_callback(1)
class AttachmentChecks(unittest.IsolatedAsyncioTestCase):
 def fixture(self):
  delivered=[];posts=[];marked=[];events=[];done=threading.Event()
  class Engine:
   def process(self,mic,ref):return mic
   def close(self):pass
  def deliver(*args):delivered.append(args);done.set() if len(delivered)==4 else None
  inp=NS(_audio_in_callback=lambda *a:None,record_capture=lambda *a:marked.append(a),deliver_capture=deliver,capture_ack=lambda:(None,99));out=NS(callback=lambda *a:(bytes(3840),99));state=NS(loop=asyncio.get_running_loop(),input_callback_periods=4,post=lambda *a,**kw:posts.append((a,kw)));transport=NS(input=lambda:inp,output=lambda:out,state=state,close=AsyncMock());processor=NS(pipeline_failed=asyncio.Event());echo=EchoControl(Engine())
  attach((None,None,transport,processor),NS(output_rate=48000,output_channels=2),echo,NS(emit=lambda *a,**kw:events.append((a,kw))),threaded_capture=True)
  return inp,transport,processor,echo,delivered,posts,marked,done
 async def test_every_original_sample_and_ADC_offset_reaches_20ms_processing(self):
  inp,t,p,echo,delivered,posts,marked,done=self.fixture();pcm=b''.join(bytes([i])*640 for i in range(1,5))
  try:
   self.assertEqual(inp._audio_in_callback(pcm,1280,dict(current_time=10.08,input_buffer_adc_time=10),0),(None,99));self.assertTrue(await asyncio.to_thread(done.wait,1));self.assertEqual(b''.join(a[0] for a in delivered),pcm);self.assertEqual([a[1] for a in delivered],[320]*4);self.assertEqual([a[2]['input_buffer_adc_time'] for a in delivered],[10,10.02,10.04,10.06]);self.assertEqual(len(marked),1);self.assertFalse(posts);self.assertLessEqual(echo.capture_worker.peak,8)
  finally:await t.close()
 async def test_batched_device_overflow_still_stops_before_any_audio_delivery(self):
  inp,t,p,echo,delivered,posts,marked,done=self.fixture()
  try:
   inp._audio_in_callback(bytes(2560),1280,dict(current_time=10.08,input_buffer_adc_time=10),2);await asyncio.sleep(.01);self.assertTrue(p.pipeline_failed.is_set());self.assertFalse(delivered);self.assertEqual(posts[0][1]['code'],'speaker_aec_capture_discontinuity')
  finally:await t.close()
 async def test_partial_host_block_is_rejected_without_padding_or_truncation(self):
  inp,t,p,echo,delivered,posts,marked,done=self.fixture()
  try:
   inp._audio_in_callback(bytes(1920),960,dict(current_time=10.08,input_buffer_adc_time=10),0);await asyncio.sleep(.01);self.assertTrue(p.pipeline_failed.is_set());self.assertFalse(delivered)
  finally:await t.close()
