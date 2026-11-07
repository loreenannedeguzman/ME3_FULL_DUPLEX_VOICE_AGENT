"""Misnamed MP4 audio through the actual local player/MCP, never a speaker."""
import asyncio,hashlib,io,os,shutil,sys,tempfile,unittest
from unittest.mock import patch
from me3_assistant.music import decoder_input
from tests import test_assistant_music as fixture
from me3_assistant.registry import Registry

class ContainerSelectionTests(unittest.TestCase):
 def test_buffered_header_read_leaves_inherited_descriptor_at_byte_zero(self):
  with tempfile.TemporaryDirectory() as folder:
   from pathlib import Path
   p=Path(folder)/'fixture.wav';header=b'RIFF1234WAVE';p.write_bytes(header+bytes(12000))
   # Production Catalog.open is read-only BufferedReader. A writable
   # BufferedRandom flush/close is a different descriptor-sharing contract.
   with p.open('rb') as f:
    decoder_input(f,'.wav');self.assertEqual(os.read(f.fileno(),12),header)
 def test_original_formats_keep_pipe_only_and_restore_file_position(self):
  for suffix,fmt in [('.mp3','mp3'),('.wav','wav'),('.flac','flac'),('.ogg','ogg')]:
   f=io.BytesIO(b'fixed local fixture');f.seek(3)
   self.assertEqual(decoder_input(f,suffix),['-protocol_whitelist','pipe','-f',fmt,'-i','pipe:0']);self.assertEqual(f.tell(),0)
 def test_misnamed_MOV_uses_only_inherited_descriptor_and_blocks_external_refs(self):
  with patch('me3_assistant.music.sys.platform','linux'):
   args=decoder_input(io.BytesIO(b'\x00\x00\x00\x18ftypM4A '),'.mp3')
  self.assertEqual(args,['-protocol_whitelist','file','-f','mov','-enable_drefs','0','-use_absolute_path','0','-i','/proc/self/fd/0'])
 def test_non_linux_refuses_unverified_seekable_MOV_path(self):
  with patch('me3_assistant.music.sys.platform','win32'):
   with self.assertRaisesRegex(ValueError,'seekable_media_requires_linux_descriptor'):decoder_input(io.BytesIO(b'\x00\x00\x00\x18ftypM4A '),'.mp3')

class ContainerMCPTests(unittest.IsolatedAsyncioTestCase):
 asyncSetUp=fixture.MusicMCPTests.asyncSetUp
 asyncTearDown=fixture.MusicMCPTests.asyncTearDown
 setup_actions=fixture.MusicMCPTests.setup_actions
 @unittest.skipUnless(shutil.which('ffmpeg') and sys.platform.startswith('linux'),'Requires FFmpeg and Linux inherited seekable descriptor; Pi target')
 async def test_real_trailing_index_M4A_named_MP3_plays_verifies_and_closes(self):
  p=self.media/'Fixture.m4a'
  proc=await asyncio.create_subprocess_exec(shutil.which('ffmpeg'),'-nostdin','-loglevel','error','-i',str(self.media/'Study.wav'),'-codec:a','aac',str(p),stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
  self.assertEqual(await asyncio.wait_for(proc.wait(),10),0)
  raw=p.read_bytes();self.assertGreater(raw.find(b'moov'),raw.find(b'mdat'))
  target=self.media/'Misnamed.mp3';p.rename(target);before=hashlib.sha256(target.read_bytes()).hexdigest()
  async with Registry(self.events,self.cfg,music_bus=self.bus.path) as r:
   a=self.setup_actions(r);prompt=await a.propose('propose_music_control',{'command':'play','track_id':target.name},1)
   pending=a.pending.copy();answer=await a.confirm(fixture.history('Confirm',prompt),1)
   self.assertIn('Playback started',answer);self.assertEqual(r.journal.records[pending['operation_id']]['state'],'VERIFIED');self.assertGreater(self.collected,0)
  self.assertEqual(self.bus.snapshot()['state'],'stopped');self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(),before)

if __name__=='__main__':unittest.main()
