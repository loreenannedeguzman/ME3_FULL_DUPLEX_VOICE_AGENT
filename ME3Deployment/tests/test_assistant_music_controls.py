"""User-volume semantics and retained near-end input; software fixtures only."""
import asyncio,struct,shutil,tempfile,unittest
from collections import deque
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from me3_assistant.music import intent
from me3_assistant.music_audio import MusicBus,attach_music
from me3_assistant.registry import Registry
from tests import test_assistant_music as fixture
from tests import test_completion_pipeline as pipeline

class VolumeTests(unittest.TestCase):
 def test_full_user_scale_keeps_former_point_six_gain_ceiling_and_accurate_status(self):
  with tempfile.TemporaryDirectory() as d:
   bus=MusicBus(Path(d)/'bus',True)
   try:
    for volume,expected in [(0,0),(50,3000),(100,6000)]:
     e=bus.start(volume);bus.offer(struct.pack('<h',10000)*1920,e);out=bus.mix(bytes(3840));self.assertEqual(struct.unpack('<h',out[:2])[0],expected);self.assertEqual(bus.snapshot()['volume_percent'],volume)
    self.assertEqual(intent('play','Song.mp3')['volume'],33);self.assertEqual(intent('volume',None,100.)['volume'],100)
    for v in [-1,101,100.5,True]:
     with self.assertRaises(ValueError):intent('volume',None,v)
   finally:bus.close(destroy=True)
 def test_voice_duck_and_user_mute_keep_voice_bytes_and_full_scale_headroom(self):
  with tempfile.TemporaryDirectory() as d:
   bus=MusicBus(Path(d)/'bus',True)
   try:
    e=bus.start(100);music=struct.pack('<h',10000)*1920;voice=struct.pack('<h',1000)*1920
    bus.offer(music,e);self.assertEqual(struct.unpack('<h',bus.mix(voice)[:2])[0],1900)
    bus.offer(music,e);self.assertEqual(bus.mix(voice,True),voice)
   finally:bus.close(destroy=True)

class CueTests(unittest.IsolatedAsyncioTestCase):
 async def test_optional_cues_suppressed_during_music_but_answers_and_capture_continue(self):
  with tempfile.TemporaryDirectory() as d:
   bus=MusicBus(Path(d)/'bus',True);posts=[];answer=AsyncMock();cue=AsyncMock();semantic=AsyncMock();analyze=AsyncMock(return_value=NS(name='QUIET'))
   processor=NS(vad=NS(analyze_audio=analyze),preroll=deque(maxlen=15),controller=NS(listener_cue=semantic),backchannel=cue,semantic_cue=semantic,play=answer);out=NS(callback=lambda *a:(bytes(3840),7));transport=NS(output=lambda:out,state=NS(post=lambda e,**kw:posts.append((e,kw))))
   try:
    attach_music((None,None,transport,processor),NS(output_rate=48000,output_channels=2),bus);bus.start(100)
    await processor.backchannel(1);await processor.controller.listener_cue(1);await processor.play(b'cue',1,'backchannel')
    cue.assert_not_awaited();semantic.assert_not_awaited();answer.assert_not_awaited()
    await processor.play(b'complete answer',1,'response');answer.assert_awaited_once_with(b'complete answer',1,'response')
    await processor.vad.analyze_audio(b'user speech');analyze.assert_awaited_once_with(b'user speech');self.assertEqual(processor.preroll.maxlen,50)
    bus.control(state=2);await processor.backchannel(1);cue.assert_awaited_once_with(1)
    self.assertEqual(sum(e=='assistant_music_listener_cue_suppressed' for e,k in posts),3)
   finally:bus.close(destroy=True)

class CapturePrefixTests(unittest.IsolatedAsyncioTestCase):
 asyncSetUp=pipeline.PipelineTests.asyncSetUp
 asyncTearDown=pipeline.PipelineTests.asyncTearDown
 async def test_delayed_VAD_onset_keeps_quiet_leading_word_audio_without_transcript_matching(self):
  from pipecat.frames.frames import InputAudioRawFrame
  from pipecat.processors.frame_processor import FrameDirection
  from pipecat.audio.vad.vad_analyzer import VADState
  with tempfile.TemporaryDirectory() as d:
   bus=MusicBus(Path(d)/'bus',True);self.transport.output().callback=lambda *a:(bytes(3840),7);self.transport.state.post=lambda *a,**k:None
   try:
    attach_music((self.runner,self.worker,self.transport,self.p),NS(output_rate=48000,output_channels=2),bus)
    leading=b'\x17\x00'*320;self.p.vad.state=VADState.QUIET
    for _ in range(30):await self.p.process_frame(InputAudioRawFrame(audio=leading,sample_rate=16000,num_channels=1),FrameDirection.DOWNSTREAM)
    self.p.vad.state=VADState.SPEAKING;await self.p.process_frame(InputAudioRawFrame(audio=b'\x21\x00'*320,sample_rate=16000,num_channels=1),FrameDirection.DOWNSTREAM)
    self.assertTrue(bytes(self.p.pcm).startswith(leading*30));self.assertEqual(len(self.p.pcm),31*640)
   finally:bus.close(destroy=True)

class ActualVolumeTests(unittest.IsolatedAsyncioTestCase):
 asyncSetUp=fixture.MusicMCPTests.asyncSetUp
 asyncTearDown=fixture.MusicMCPTests.asyncTearDown
 setup_actions=fixture.MusicMCPTests.setup_actions
 @unittest.skipUnless(shutil.which('ffmpeg'),'FFmpeg unavailable')
 async def test_actual_MCP_volume_one_hundred_is_accepted_executed_and_independently_read_back(self):
  async with Registry(self.events,self.cfg,music_bus=self.bus.path) as r:
   a=self.setup_actions(r)
   for args in [dict(command='play',track_id='Study.wav'),dict(command='volume',volume=100)]:
    prompt=await a.propose('propose_music_control',args,1);reply=await a.confirm(fixture.history('Confirm',prompt),1);self.assertNotIn('could not',reply)
   self.assertIn('100 percent',reply);self.assertEqual(self.bus.snapshot()['volume_percent'],100);self.assertEqual(list(r.journal.records.values())[-1]['state'],'VERIFIED')

if __name__=='__main__':unittest.main()
