"""Real temporary playlist persistence/MCP/decoder transitions, never speaker output."""
import asyncio,json,os,shutil,tempfile,unittest,uuid
from pathlib import Path
from me3_assistant.music import Catalog,intent
from me3_assistant.music_queue import Playlists,Queue
from me3_assistant.registry import Registry,ToolFailure
from tests import test_assistant_music as f

class PlaylistTests(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);f.tone(self.root/'one.wav',.05)
 def tearDown(self):self.tmp.cleanup()
 def test_read_is_noncreating_save_is_persistent_and_stale_write_rejected(self):
  p=Playlists(Catalog(self.root));self.assertEqual(p.list()['playlists'],[]);self.assertFalse(p.path.exists())
  self.assertEqual(p.save('Study',['one.wav'],0),1);self.assertEqual(Playlists(Catalog(self.root)).read('Study')['track_ids'],['one.wav'])
  before=p.path.read_bytes()
  with self.assertRaises(ValueError):p.save('Other',['one.wav'],0)
  self.assertEqual(before,p.path.read_bytes())
 def test_unrelated_or_linked_metadata_is_preserved(self):
  p=Playlists(Catalog(self.root));p.path.write_bytes(b'user-owned-unrelated-file');before=p.path.read_bytes()
  with self.assertRaises(ValueError):p.save('Study',['one.wav'],0)
  self.assertEqual(before,p.path.read_bytes());p.path.unlink();outside=self.root/'original.json';outside.write_bytes(b'user contents');os.link(outside,p.path)
  with self.assertRaises(ValueError):p.save('Study',['one.wav'],0)
  self.assertEqual(outside.read_bytes(),b'user contents')
 def test_control_arguments_reject_paths_fields_and_oversized_queues(self):
  for args in [('save_playlist',None,None,['one.wav'],'../escape'),('enqueue',None,None,[],None),('enqueue',None,None,['one.wav']*21,None),('skip','one.wav',None,None,None),('clear_queue',None,20,None,None),('play',None,None,['one.wav'],None)]:
   with self.subTest(args=args),self.assertRaises(ValueError):intent(*args)
  self.assertEqual(intent('enqueue',None,None,['one.wav'])['track_ids'],['one.wav'])
  self.assertEqual(intent('play','one.wav'),{'command':'play','track_id':'one.wav','volume':33})

class QueueMCPTests(unittest.IsolatedAsyncioTestCase):
 asyncSetUp=f.MusicMCPTests.asyncSetUp
 asyncTearDown=f.MusicMCPTests.asyncTearDown
 setup_actions=f.MusicMCPTests.setup_actions
 def config(self):return {**self.cfg,'servers':[{**self.cfg['servers'][0],'read_tools':['list_tracks','get_playback_status','get_queue','list_playlists','read_playlist']}]}
 async def perform(self,a,command,**args):
  prompt=await a.propose('propose_music_control',{'command':command,**args},1);self.assertIsNotNone(a.pending,prompt);p=dict(a.pending);reply=await a.confirm(f.history('Confirm',prompt),1);self.assertEqual(a.journal.records[p['operation_id']]['state'],'VERIFIED',reply);return p,reply
 async def test_saved_playlist_actual_MCP_survives_fresh_service_and_no_decoder_starts(self):
  cfg=self.config()
  async with Registry(self.events,cfg) as r:
   a=self.setup_actions(r,4);self.assertEqual((await r.read('music__list_playlists',1,{}))['playlists'],[])
   await self.perform(a,'save_playlist',playlist_name='Study mix',track_ids=['Study.wav'])
   self.assertEqual((await r.read('music__read_playlist',1,{'playlist_name':'Study mix'}))['track_ids'],['Study.wav'])
  async with Registry(self.events,cfg) as r:
   self.assertEqual((await r.read('music__list_playlists',1,{}))['playlists'],[{'name':'Study mix','track_count':1}]);self.assertEqual((await r.read('music__get_playback_status',1,{}))['state'],'unavailable')
 @unittest.skipUnless(shutil.which('ffmpeg'),'FFmpeg unavailable')
 async def test_actual_playlist_play_skip_clear_stop_and_duplicate_do_not_repeat(self):
  f.tone(self.media/'Second.wav',2)
  async with Registry(self.events,self.config(),music_bus=self.bus.path) as r:
   a=self.setup_actions(r,10);await self.perform(a,'save_playlist',playlist_name='Pair',track_ids=['Study.wav','Second.wav']);await self.perform(a,'play_playlist',playlist_name='Pair',volume=20)
   self.assertEqual((await r.read('music__get_queue',1,{}))['track_ids'],['Second.wav']);await self.perform(a,'skip')
   status=await r.read('music__get_playback_status',1,{});self.assertEqual(status['track_id'],'Second.wav');epoch=status['epoch']
   await self.perform(a,'enqueue',track_ids=['Study.wav']);pending,_=await self.perform(a,'clear_queue')
   self.assertEqual((await r.read('music__get_queue',1,{}))['track_ids'],[]);self.assertEqual((await r.read('music__get_playback_status',1,{}))['epoch'],epoch)
   duplicate=await r.rpc('music','commit_music_control',1,{'operation_id':pending['operation_id'],'token':pending['proposal']['token'],'confirmation':'confirm'});self.assertTrue(duplicate['duplicate']);await self.perform(a,'stop');self.assertGreater(self.collected,0)
 @unittest.skipUnless(shutil.which('ffmpeg'),'FFmpeg unavailable')
 async def test_real_decoder_end_of_track_advances_confirmed_order_then_stays_ended(self):
  f.tone(self.media/'ShortA.wav',.2);f.tone(self.media/'ShortB.wav',.2)
  async with Registry(self.events,self.config(),music_bus=self.bus.path) as r:
   a=self.setup_actions(r,4);await self.perform(a,'save_playlist',playlist_name='Short pair',track_ids=['ShortA.wav','ShortB.wav']);await self.perform(a,'play_playlist',playlist_name='Short pair')
   async with asyncio.timeout(8):
    while True:
     state=await r.read('music__get_playback_status',1,{})
     if state['track_id']=='ShortB.wav' and state['state']=='ended':break
     await asyncio.sleep(.03)
   queue=await r.read('music__get_queue',1,{});self.assertEqual(queue['track_ids'],[]);self.assertIsNone(queue['error']);self.assertGreater(self.collected,0)
  self.assertEqual(self.bus.snapshot()['state'],'stopped')
 async def test_unconfirmed_or_stale_queue_proposal_has_no_effect(self):
  async with Registry(self.events,self.config(),music_bus=self.bus.path) as r:
   a=self.setup_actions(r,6);await a.propose('propose_music_control',{'command':'enqueue','track_ids':['Study.wav']},1);a.cancel();self.assertEqual((await r.read('music__get_queue',1,{}))['track_ids'],[])
   first=await r.rpc('music','prepare_music_control',1,{'command':'enqueue','track_ids':['Study.wav'],'operation_id':uuid.uuid4().hex})
   second=await r.rpc('music','prepare_music_control',1,{'command':'enqueue','track_ids':['Study.wav'],'operation_id':uuid.uuid4().hex})
   with self.assertRaises(ToolFailure):await r.rpc('music','commit_music_control',1,{'operation_id':first['operation_id'],'token':first['token'],'confirmation':'confirm'})
   self.assertEqual((await r.read('music__get_queue',1,{}))['track_ids'],[]);self.assertEqual(self.bus.snapshot()['state'],'stopped')
 @unittest.skipUnless(shutil.which('ffmpeg'),'FFmpeg unavailable')
 async def test_changed_queued_file_stops_with_explicit_error_and_no_automatic_retry(self):
  f.tone(self.media/'Later.wav',1)
  async with Registry(self.events,self.config(),music_bus=self.bus.path) as r:
   a=self.setup_actions(r,5);await self.perform(a,'play',track_id='Study.wav');await self.perform(a,'enqueue',track_ids=['Later.wav']);(self.media/'Later.wav').unlink()
   prompt=await a.propose('propose_music_control',{'command':'skip'},1);reply=await a.confirm(f.history('Confirm',prompt),1);self.assertIn('not repeat',reply)
   state=await r.read('music__get_queue',1,{});self.assertEqual(state['error'],'queue_track_failed');self.assertFalse(state['autoplay']);before=dict(a.journal.records);await asyncio.sleep(.08);self.assertEqual(before,a.journal.records)
 async def test_unknown_playlist_and_private_commit_stay_unavailable(self):
  async with Registry(self.events,self.config(),music_bus=self.bus.path) as r:
   with self.assertRaises(ToolFailure):await r.read('music__read_playlist',1,{'playlist_name':'Missing'})
   with self.assertRaises(ToolFailure):await r.read('music__commit_music_control',1,{})
   self.assertFalse((self.media/'.me3-playlists.json').exists());self.assertEqual(self.bus.snapshot()['state'],'stopped')

if __name__=='__main__':unittest.main()
