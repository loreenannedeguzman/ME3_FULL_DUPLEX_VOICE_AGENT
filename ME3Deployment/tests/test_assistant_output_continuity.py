"""No device/provider:60ms output final-PCM reference, fences and current-turn scope."""
import asyncio,importlib.util,json,unittest
from array import array
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,patch
from duplex_audio import DuplexState,PlayoutBuffer
from me3_completion.playback import SuspendableBuffer
from me3_assistant.acoustic_echo import EchoControl
from me3_assistant.groq_json import json_messages,final_speech_messages
from tests.test_assistant_acoustic_echo import Engine,stereo
from tests.test_assistant_groq_json import DECL

class OutputReferenceTests(unittest.TestCase):
 def echo(self):e=EchoControl(Engine());e.output_callback_periods=3;return e
 def test_final60ms_pcm_is_split_into_exact_timestamped20ms_references(self):
  e=self.echo();chunks=[stereo([v]*960) for v in (1000,2000,3000)]
  self.assertFalse(e.playback(b''.join(chunks),{'current_time':10,'output_buffer_dac_time':10.06},now=50))
  self.assertEqual(len(e.reference.chunks),3)
  for i,value in enumerate((1000,2000,3000)):
   data,covered=e.reference.frame(50.06+i*.02)
   self.assertEqual((covered,len(data)),(320,640));self.assertEqual(array('h',data),array('h',[value]*320))
  self.assertEqual(e.reference.frame(50.13),(bytes(640),0));e.close()
 def test_underflow_with_silent_prefix_and_later_active_pcm_remains_fatal(self):
  for block in (bytes(3840)+stereo([1000]*960)+bytes(3840),stereo([1000]*2880)):
   e=self.echo()
   for i in range(20):e.playback(bytes(11520),{'current_time':0,'output_buffer_dac_time':0},now=i*.06)
   before=tuple(e.reference.chunks)
   with self.assertRaisesRegex(ValueError,'playback_discontinuity'):e.playback(block,{'current_time':0,'output_buffer_dac_time':0},now=1.2,status=4)
   self.assertEqual(tuple(e.reference.chunks),before);e.close()
 def test_idle_underflow_counts_one_host_status_and_burst_limit_still_stops(self):
  e=self.echo()
  for i in range(20):e.playback(bytes(11520),{'current_time':0,'output_buffer_dac_time':0},now=i*.06)
  for now in (1.2,1.26):
   self.assertTrue(e.playback(bytes(11520),{'current_time':0,'output_buffer_dac_time':0},now=now,status=4))
  self.assertEqual(e.reference.idle_underflows,2)
  with self.assertRaisesRegex(ValueError,'idle_underflow_limit'):e.playback(bytes(11520),{'current_time':0,'output_buffer_dac_time':0},now=1.32,status=4)
  e.close()
 def test_unknown_status_clock_reversal_and_wrong_frames_fail_without_capture_fallback(self):
  for status in (1,8,12):
   e=self.echo()
   with self.assertRaises(ValueError):e.playback(bytes(11520),{'current_time':0,'output_buffer_dac_time':0},now=1,status=status)
   e.close()
  e=self.echo()
  for size in (3840,7680,11519,15360):
   with self.assertRaises(ValueError):e.playback(bytes(size),{'current_time':0,'output_buffer_dac_time':0},now=1)
  e.playback(bytes(11520),{'current_time':0,'output_buffer_dac_time':0},now=1)
  with self.assertRaises(ValueError):e.playback(bytes(11520),{'current_time':0,'output_buffer_dac_time':0},now=1)
  e.close()
 def test_60ms_buffer_cancellation_discards_old_words_and_resume_keeps_same_epoch(self):
  b=SuspendableBuffer(PlayoutBuffer(23040));b.enable_smoothing(48000,2)
  b.offer(stereo([1000]*5760),0);self.assertEqual(b.take(11520)[2],11520);b.suspend()
  out,epoch,played,queued=b.take(11520);self.assertEqual((epoch,played,queued),(0,0,11520));self.assertFalse(any(out[960:]))
  b.resume();self.assertEqual(b.take(11520)[1:3],(0,11520))
  b.offer(stereo([2000]*2880),0);b.suspend();self.assertEqual(b.clear(),(1,11520))
  self.assertEqual(b.offer(stereo([3000]*2880),0),'stale');self.assertEqual(b.take(11520)[0],bytes(11520))
 def test_normal_profile_is_explicit_and_legacy_remains20ms(self):
  s=DuplexState(NS(output_rate=48000,output_channels=2),NS())
  self.assertEqual(s.output_callback_periods,1);s.configure_output_callback(3);self.assertEqual(s.output_callback_periods,3)
  for invalid in (True,0,2,4,3.0):
   with self.assertRaises(ValueError):s.configure_output_callback(invalid)
  s.loop=NS()
  with self.assertRaises(ValueError):s.configure_output_callback(1)
  with self.assertRaises(ValueError):DuplexState(NS(output_rate=16000,output_channels=1),NS()).configure_output_callback(3)

@unittest.skipUnless(importlib.util.find_spec('pyaudio'),'PyAudio transport dependency required')
class OutputOpenTests(unittest.IsolatedAsyncioTestCase):
 async def test_real_transport_requests60ms_output_and_keeps20ms_input_configuration(self):
  import pyaudio
  from duplex_audio import make_duplex_transport
  from pipecat.transports.base_output import BaseOutputTransport
  stream=NS(get_output_latency=lambda:.06);seen=[]
  class Audio:
   def open(self,**kw):seen.append(kw);return stream
  settings=NS(output_rate=48000,output_channels=2,input_device=4,output_device=5)
  events=NS(emit=lambda *a,**k:None)
  with patch.object(pyaudio,'PyAudio',return_value=Audio()),patch.object(BaseOutputTransport,'setup',AsyncMock()),patch.object(BaseOutputTransport,'sample_rate',property(lambda self:48000)):
   transport,state=make_duplex_transport(settings,events);state.configure_output_callback(3);out=transport.output()
   await out.setup(None)
   self.assertEqual((seen[0]['frames_per_buffer'],seen[0]['start'],seen[0]['output_device_index']),(2880,False,5))
   state.buffer.offer(stereo([1000]*2880),0)
   pcm,flag=out.callback(None,2880,{},0);self.assertEqual(pcm,stereo([1000]*2880));self.assertEqual(flag,pyaudio.paContinue)

class CurrentRequestTests(unittest.TestCase):
 def history(self):return [
  {'type':'user_input','content':[{'type':'text','text':'Work out 41 times 6.'}]},
  {'type':'assistant_response','content':[{'type':'text','text':'41 times 6 is 246.'}]},
  {'type':'user_input','content':[{'type':'text','text':'There are many documents in my folder; what can I do?'}]}]
 def test_new_topic_has_one_current_request_and_prior_calculation_is_context_only(self):
  h=self.history();before=json.dumps(h);wire=json_messages(h,DECL,[]);packet=json.loads(wire[1]['content'])
  self.assertEqual(packet['request_to_complete'],h[-1]['content']);self.assertEqual(packet['context_only_entries'],2)
  self.assertFalse(packet['prior_turns_are_pending_requests']);self.assertEqual(len(packet['conversation_and_observations']),2)
  self.assertIn('246',packet['conversation_and_observations'][1]['content']);self.assertIn('not a backlog',wire[0]['content'])
  self.assertEqual(json.dumps(h),before)
 def test_followup_keeps_referents_but_final_does_not_reinstate_prior_work(self):
  h=self.history();h[-1]['content'][0]['text']='Use 72 instead of 41 and calculate that again.'
  wire=final_speech_messages(h,[]);p=json.loads(wire[1]['content'])
  self.assertEqual(p['context_only_entries'],2);self.assertIn('41',p['conversation_and_observations'][0]['content'])
  self.assertIn('72',str(p['request_to_complete']));self.assertIn('Earlier requests are not pending tasks',wire[0]['content'])
  self.assertEqual(p['permitted_operations'],[])

