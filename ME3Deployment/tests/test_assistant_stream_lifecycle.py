"""A stopped window may close; active, abnormal or reference losses stay fatal."""
import asyncio,tempfile,unittest,sys
from pathlib import Path
from types import SimpleNamespace
from me3_stream.browser import Broker
from me3_stream.audio import PCMInput
from task_mcp.client import ROOT
class Events:
 def __init__(self):self.rows=[]
 def emit(self,event,**fields):self.rows.append(dict(event=event,**fields))
class Lifecycle(unittest.IsolatedAsyncioTestCase):
 def make(self,*,command='stop',playing=False,code=0,reference=True,current=True):
  e=Events();p=PCMInput();b=Broker(Path(tempfile.gettempdir()),pcm=p,reference_ready=lambda:reference,voice_active=lambda:True,current=lambda:current,events=e)
  b.last_verified_command=command;b.playing=playing;b.process=SimpleNamespace(returncode=code)
  b.reader=asyncio.get_running_loop().create_future();b.reader.set_result(None)
  return b,e
 async def watch(self,b):await asyncio.wait_for(b.watch_resources(),1)
 async def test_only_verified_stopped_clean_close_retires_without_session_fault_or_replay(self):
  b,e=self.make();calls=[]
  async def close(**kw):calls.append(kw)
  b.close_resources=close
  await self.watch(b);self.assertFalse(b.fault.is_set());self.assertEqual(calls,[{'preserve_owner':True}]);self.assertTrue(any(r['event']=='assistant_stopped_stream_window_retired' for r in e.rows));self.assertFalse(any(r['event']=='assistant_stream_audio_failed' for r in e.rows))
 async def test_active_player_clean_close_is_still_fatal(self):
  b,e=self.make(command='play',playing=True);await self.watch(b);self.assertTrue(b.fault.is_set());self.assertEqual(e.rows[-1]['reason'],'monitor_reader_ended')
 async def test_unverified_stopped_state_cannot_release_owner(self):
  b,e=self.make(command=None);await self.watch(b);self.assertTrue(b.fault.is_set())
 async def test_abnormal_exit_remains_fatal_after_stop(self):
  b,e=self.make(code=9);await self.watch(b);self.assertTrue(b.fault.is_set())
 async def test_reference_loss_remains_fatal_after_stop(self):
  b,e=self.make(reference=False);await self.watch(b);self.assertTrue(b.fault.is_set());self.assertEqual(e.rows[-1]['reason'],'final_AEC_reference_lost')
 async def test_cleanup_error_remains_fatal(self):
  b,e=self.make()
  async def close(**kw):raise ValueError('fixture_cleanup_failed')
  b.close_resources=close;await self.watch(b);self.assertTrue(b.fault.is_set());self.assertEqual(e.rows[-1]['reason'],'stopped_player_cleanup_unverified')
 @unittest.skipIf(sys.platform=='win32','Actual private Unix socket ownership runs on Pi')
 async def test_retirement_clears_PCM_keeps_owner_and_closes_finally(self):
  e=Events();p=PCMInput();g=p.begin();p.offer(bytes(1920),g)
  with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as folder:
   b=Broker(Path(folder),pcm=p,reference_ready=lambda:True,voice_active=lambda:True,current=lambda:True,events=e);b.last_verified_command='stop'
   await b.__aenter__();owner=b.owner
   await b.close_resources(preserve_owner=True)
   self.assertFalse(b.closed);self.assertTrue(b.stopped_cleanly);self.assertTrue(b.resources_closed);self.assertEqual(b.owner,owner);self.assertTrue(b.path.exists());self.assertIsNone(b.process);self.assertFalse(p.active);self.assertEqual(len(p.buffer),0);self.assertFalse(p.offer(bytes(1920),g));self.assertFalse(b.fault.is_set())
   await b.close();self.assertTrue(b.closed);self.assertTrue(b.stopped_cleanly);self.assertFalse(b.path.exists());self.assertFalse(b.socket.exists())
 async def test_owner_preservation_rejects_active_or_unverified_states(self):
  for command,playing in [('play',True),(None,False),('pause',False),('stop',True)]:
   b,_=self.make(command=command,playing=playing)
   with self.assertRaisesRegex(ValueError,'verified_stopped_player_required'):await b.close_resources(preserve_owner=True)
