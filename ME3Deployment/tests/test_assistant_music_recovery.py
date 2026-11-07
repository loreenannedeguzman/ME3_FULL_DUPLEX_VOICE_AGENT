"""Append-only volatile-session retirement; no retry or persistent-effect waiver."""
import json,shutil,tempfile,unittest,uuid
from pathlib import Path
from unittest.mock import AsyncMock
from me3_assistant.actions import Actions,Journal
from me3_assistant.registry import Registry
from tests import test_assistant_standing_music as standing

class JournalRecoveryTests(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'journal.jsonl';self.j=Journal(self.path)
 def tearDown(self):self.tmp.cleanup()
 def uncertain(self,action='propose_music_control',owner=None):
  op=uuid.uuid4().hex;self.j.record(op,action,'PROPOSED','a'*64,**({'volatile_music_owner':owner} if owner else {}));self.j.record(op,action,'DISPATCHED','a'*64);self.j.record(op,action,'UNCERTAIN','a'*64);return op
 def test_owner_only_retirement_appends_preserves_unknown_outcome_and_survives_restart(self):
  owner=uuid.uuid4().hex;op=self.uncertain(owner=owner);before=self.path.read_bytes()
  self.assertEqual(self.j.end_music_owner(uuid.uuid4().hex),0);self.assertEqual(self.path.read_bytes(),before)
  self.assertEqual(self.j.end_music_owner(owner),1);self.assertTrue(self.path.read_bytes().startswith(before));self.assertEqual(self.j.records[op]['state'],'SESSION_ENDED');self.assertFalse(Journal(self.path).unresolved())
  rows=[json.loads(s) for s in self.path.read_text().splitlines()];self.assertEqual([r['state'] for r in rows],['PROPOSED','DISPATCHED','UNCERTAIN','SESSION_ENDED']);self.assertNotIn('VERIFIED',[r['state'] for r in rows])
 def test_legacy_unknown_music_and_persistent_writes_remain_unresolved(self):
  self.uncertain();self.uncertain('propose_shopping_item');before=self.path.read_bytes();self.assertEqual(self.j.end_music_owner(uuid.uuid4().hex),0);self.assertEqual(self.path.read_bytes(),before);self.assertEqual(len(self.j.unresolved()),2)
 def test_owner_cannot_change_or_be_injected_into_other_actions(self):
  owner=uuid.uuid4().hex;op=self.uncertain(owner=owner)
  with self.assertRaisesRegex(ValueError,'owner_changed'):self.j.record(op,'propose_music_control','UNCERTAIN','a'*64,volatile_music_owner=uuid.uuid4().hex)
  with self.assertRaises(ValueError):self.j.record(uuid.uuid4().hex,'propose_shopping_item','PROPOSED','a'*64,volatile_music_owner=owner)
  old=self.uncertain('propose_shopping_item')
  with self.assertRaises(ValueError):self.j.record(old,'propose_shopping_item','SESSION_ENDED','a'*64)

class OwnerRecoveryTests(unittest.IsolatedAsyncioTestCase):
 asyncSetUp=standing.ActualMCPTests.asyncSetUp
 asyncTearDown=standing.ActualMCPTests.asyncTearDown
 setup=standing.ActualMCPTests.setup
 @unittest.skipUnless(shutil.which('ffmpeg'),'FFmpeg unavailable')
 async def test_uncertain_play_stays_blocked_until_actual_MCP_closes_then_fresh_owner_request_can_play(self):
  async with Registry(self.events,self.cfg,music_bus=self.bus.path) as r:
   self.setup(r);a=Actions(r,r.config['actions'],r.budget,r.journal,self.events,lambda g:True,allowed=r.allowed_actions);a.adapters['propose_music_control'].verify=AsyncMock(return_value=False)
   reply=await a.propose('propose_music_control',dict(command='play',track_id='Study.wav'),1);self.assertIn('not repeat',reply);op=next(iter(r.journal.records));self.assertEqual(r.journal.records[op]['state'],'UNCERTAIN')
   self.assertIn('unverified outcome',await a.propose('propose_music_control',dict(command='play',track_id='Study.wav'),1));self.assertEqual(a.budget._used(),1)
  self.assertEqual(self.bus.snapshot()['state'],'stopped');self.assertEqual(Journal(r.journal.path).records[op]['state'],'SESSION_ENDED')
  prior_bytes=r.journal.path.read_bytes()
  async with Registry(self.events,self.cfg,music_bus=self.bus.path) as fresh:
   self.setup(fresh);fresh.journal=Journal(r.journal.path);b=Actions(fresh,fresh.config['actions'],fresh.budget,fresh.journal,self.events,lambda g:True,allowed=fresh.allowed_actions)
   status=await b.status(1);self.assertEqual(status['status'],'previous_music_session_ended');self.assertEqual(status['original_execution_outcome'],'not_verified');self.assertFalse(status['write_retried'])
   reply=await b.propose('propose_music_control',dict(command='play',track_id='Study.wav'),1);self.assertIn('Playback started',reply);self.assertEqual(b.budget._used(),2);self.assertEqual(fresh.journal.records[op]['state'],'SESSION_ENDED');self.assertTrue(fresh.journal.path.read_bytes().startswith(prior_bytes));self.assertEqual(len(fresh.journal.records),2)
 async def test_uncertain_saved_playlist_still_blocks_after_actual_MCP_close(self):
  async with Registry(self.events,self.cfg,music_bus=self.bus.path) as r:
   self.setup(r);a=Actions(r,r.config['actions'],r.budget,r.journal,self.events,lambda g:True,allowed=r.allowed_actions);a.adapters['propose_music_control'].verify=AsyncMock(return_value=False)
   await a.propose('propose_music_control',dict(command='save_playlist',playlist_name='One',track_ids=['Study.wav']),1)
  j=Journal(r.journal.path);self.assertEqual(len(j.unresolved()),1);self.assertNotIn('volatile_music_owner',j.unresolved()[0])

if __name__=='__main__':unittest.main()
