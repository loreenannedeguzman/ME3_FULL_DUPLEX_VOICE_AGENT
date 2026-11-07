"""No-provider waveform and acknowledgment ownership checks."""
import asyncio,unittest
from array import array
from types import SimpleNamespace
from duplex_audio import PlayoutBuffer
from me3_completion.playback import SuspendableBuffer,PlaybackGate,TransitionEnvelope
from me3_assistant.overlap import attach
from tests import test_agent as fixtures

def pcm(frames,left=24000,right=-18000):
 return array('h',[v for _ in range(frames) for v in (left,right)]).tobytes()
def samples(data):a=array('h');a.frombytes(data);return a

class EnvelopeTests(unittest.TestCase):
 def setup_buffer(self):
  b=SuspendableBuffer(PlayoutBuffer(48000));b.enable_smoothing(48000,2);return b
 def test_suspension_removes_abrupt_edge_without_consuming_queued_speech(self):
  b=self.setup_buffer();b.offer(pcm(1920),0);head=b.take(3840)[0];self.assertEqual(samples(head)[-2:],array('h',[24000,-18000]))
  before=b.delegate._size;b.suspend();out,e,played,queued=b.take(3840);a=samples(out)
  self.assertEqual((e,played,queued),(0,0,before));self.assertLessEqual(abs(a[0]-24000),101)
  self.assertTrue(all(abs(a[i]-a[i-2])<=101 for i in range(2,len(a))))
  self.assertEqual(a[478:],array('h',[0])*(len(a)-478));self.assertEqual(b.take(3840)[0],bytes(3840))
  b.resume();out,e,played,queued=b.take(3840);a=samples(out)
  self.assertEqual(a[:2],array('h',[0,0]));self.assertEqual(a[478:],samples(pcm(960))[478:]);self.assertEqual((e,played,queued),(0,3840,0))
 def test_clear_cancels_tail_and_late_epoch_even_while_suspended(self):
  b=self.setup_buffer();b.offer(pcm(1920),0);b.take(3840);b.suspend()
  self.assertNotEqual(b.take(40)[0],bytes(40));epoch,discarded=b.clear()
  self.assertEqual((epoch,discarded),(1,3840));self.assertEqual(b.offer(pcm(10),0),'stale')
  self.assertEqual(b.take(3840),(bytes(3840),1,0,0));b.resume()
  b.offer(pcm(960,left=9000,right=7000),1);a=samples(b.take(3840)[0]);self.assertEqual(a[:2],array('h',[0,0]));self.assertEqual(a[-2:],array('h',[9000,7000]))
 def test_ramp_is_bounded_across_small_callback_chunks_and_idempotent_suspend(self):
  b=self.setup_buffer();b.offer(pcm(1500),0);b.take(3840);b.suspend();outs=[]
  for _ in range(25):
   b.suspend();outs.extend(samples(b.take(40)[0]))
  self.assertEqual(outs[478:],[0]*22);self.assertTrue(all(abs(outs[i]-outs[i-2])<=101 for i in range(2,len(outs))))
 def test_wrong_alignment_is_rejected_before_audio_is_consumed(self):
  b=self.setup_buffer();b.offer(pcm(500),0);before=b.delegate._size
  with self.assertRaises(ValueError):b.take(3)
  self.assertEqual(b.delegate._size,before)
  for rate,ch in [(0,2),(48000,0),(True,2),(48000,True)]:
   with self.assertRaises(ValueError):TransitionEnvelope(rate,ch)
 def test_legacy_buffer_retains_exact_original_bytes(self):
  b=SuspendableBuffer(PlayoutBuffer(20));b.offer(b'abcd',0);b.suspend();self.assertEqual(b.take(4),(bytes(4),0,0,4));b.resume();self.assertEqual(b.take(4)[0],b'abcd')

class AcknowledgmentTests(fixtures.OverlapTests):
 def setup_processor(self):
  e=super().setup_processor();self.b=SuspendableBuffer(PlayoutBuffer(48000));self.b.enable_smoothing(48000,2)
  self.p.gate=PlaybackGate(self.b);self.b.offer(pcm(1920),0);self.b.take(3840);self.p.gate.suspend();self.b.take(3840)
  def resume():self.resumed=True;self.b.resume();self.p.gate.ready.set()
  self.p.gate.resume=resume;attach(self.p,self.client,self.provider,e);return e
 async def test_actual_gate_resumes_same_PCM_after_one_validated_split_acknowledgment(self):
  self.setup_processor();self.text='uh huh';before=self.b.delegate._size;count=0;old=self.client.call
  async def call(*a,**k):
   nonlocal count
   count+=1;return await old(*a,**k)
  self.client.call=call
  wait=asyncio.create_task(self.p.gate.wait());await asyncio.sleep(0);self.assertFalse(wait.done())
  await self.p.classify_overlap(1,b'audio',.6,3,self.old_task);await asyncio.wait_for(wait,.5)
  self.assertEqual((self.g,count,self.p.controller.listener_ack_count),(3,1,1));self.assertFalse(self.b.paused)
  out,e,played,queued=self.b.take(before);self.assertEqual((e,played,queued),(0,before,0));self.assertEqual(samples(out)[-2:],array('h',[24000,-18000]))
  self.assertTrue(any(r['event']=='completion_user_backchannel' for r in self.rows))
 async def test_canceled_probe_never_releases_suspended_output(self):
  self.setup_processor();started=asyncio.Event()
  async def call(*a,**k):started.set();await asyncio.Event().wait()
  self.client.call=call;task=asyncio.create_task(self.p.classify_overlap(1,b'audio',.6,3,self.old_task));await started.wait();task.cancel()
  with self.assertRaises(asyncio.CancelledError):await task
  self.assertTrue(self.b.paused);self.assertFalse(self.p.gate.ready.is_set());self.assertFalse(self.resumed)

