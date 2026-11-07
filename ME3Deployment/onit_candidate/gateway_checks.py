"""Guarded actions using temporary real MCP services; no live provider/audio."""
import asyncio,json,sys,unittest,uuid
from pathlib import Path
from unittest.mock import AsyncMock
from me3_assistant.actions import Journal
from me3_assistant.registry import Registry
from me3_assistant.standing_music import initialize
from onit_candidate.gateway import Gateway,Ledger,fingerprint
from onit_candidate.engine import Engine
from onit_candidate.protocol import CandidateError
from onit_candidate.checks import FixtureTransport,response,native,Budget as ModelBudget
from tests import test_assistant_mode as f
from tests import test_assistant_music as music
from tests.test_assistant_standing_music import grant
from task_mcp.client import ROOT
ACTION='propose_music_control'
def args(name='Gateway One'):return dict(command='save_playlist',playlist_name=name,track_ids=['Study.wav'])
def call(name=ACTION,arguments=None,identity='gateway_call'):return dict(id=identity,name=name,args=arguments or args())
class GatewayMCPChecks(unittest.IsolatedAsyncioTestCase):
 asyncSetUp=music.MusicMCPTests.asyncSetUp
 asyncTearDown=music.MusicMCPTests.asyncTearDown
 def setup_gateway(self,r,current=lambda g:True):
  r.session_grant=grant(self.root);r.standing_music_counter=self.root/'standing-music-actions.json';initialize(r.standing_music_counter);r.budget=f.FixtureBudget(0);r.journal=Journal(self.root/'action-journal.jsonl');r.allowed_actions=[ACTION]
  gw=Gateway(ROOT,r,r.budget,r.journal,self.events,current,self.root/'goal-ledger.jsonl',action_scope={ACTION:{'command':['save_playlist']}});gw.activate(uuid.uuid4().hex,1);return gw
 def schemas(self,r,gw):return {d['name']:d['parameters'] for d in r.declarations()+gw.declarations()}
 def playlists(self):return json.loads((self.media/'.me3-playlists.json').read_bytes())['playlists'] if (self.media/'.me3-playlists.json').exists() else {}
 async def test_actual_action_is_independently_verified_and_duplicate_is_suppressed(self):
  async with Registry(self.events,self.cfg) as r:
   gw=self.setup_gateway(r);result=await gw.execute(ACTION,args(),1);self.assertEqual(result['status'],'verified');self.assertEqual(r.calls,3);before=r.calls
   again=await gw.execute(ACTION,args(),1);self.assertTrue(again['duplicate_suppressed']);self.assertEqual(r.calls,before+1);self.assertEqual(gw.actions.budget._used(),1);self.assertEqual(self.playlists(),{'Gateway One':['Study.wav']});self.assertEqual(r.budget.used,0)
 async def test_invalid_later_tool_and_duplicate_batch_prevent_every_effect(self):
  async with Registry(self.events,self.cfg) as r:
   gw=self.setup_gateway(r);schemas=self.schemas(r,gw)
   for batch in [[call(),call('commit_music_control',{},'bad')],[call(),call(identity='repeat')],[call(),call('music__get_playback_status',{'generation':1},'injected')]]:
    with self.assertRaises(Exception):gw.validate_batch(batch,schemas)
   self.assertEqual(r.calls,0);self.assertEqual(gw.actions.budget._used(),0);self.assertFalse(self.playlists())
 async def test_scope_can_only_restrict_existing_authority(self):
  async with Registry(self.events,self.cfg) as r:
   gw=self.setup_gateway(r)
   with self.assertRaises(Exception):gw.validate_batch([call(arguments={'command':'play','track_id':'Study.wav'})],self.schemas(r,gw))
   r.session_grant['allowed_actions']=[]
   with self.assertRaises(CandidateError):Gateway(ROOT,r,r.budget,r.journal,self.events,lambda g:True,self.root/'other-ledger.jsonl',action_scope={ACTION:{'command':['save_playlist']}})
   self.assertFalse(self.playlists());self.assertEqual(r.calls,0)
 async def test_uncertain_commit_blocks_later_effect_and_readback_resolves_without_replay(self):
  async with Registry(self.events,self.cfg) as r:
   gw=self.setup_gateway(r);adapter=gw.actions.adapters[ACTION];verify=adapter.verify;adapter.verify=AsyncMock(return_value=False)
   with self.assertRaises(CandidateError):await gw.execute(ACTION,args(),1)
   with self.assertRaises(CandidateError):gw.validate_batch([call(arguments=args('Later'))],self.schemas(r,gw))
   self.assertEqual(self.playlists(),{'Gateway One':['Study.wav']});self.assertEqual(gw.actions.budget._used(),1)
   adapter.verify=verify;status=await gw.status(1);self.assertEqual(status['status'],'checked');self.assertFalse(status['write_retried']);again=await gw.execute(ACTION,args(),1);self.assertTrue(again['duplicate_suppressed']);self.assertEqual(gw.actions.budget._used(),1)
 async def test_cancellation_after_real_commit_retains_uncertain_identity_and_no_undo(self):
  async with Registry(self.events,self.cfg) as r:
   gw=self.setup_gateway(r);adapter=gw.actions.adapters[ACTION];original=adapter.execute;committed=asyncio.Event()
   async def wait_after_commit(p,g):await original(p,g);committed.set();await asyncio.Event().wait()
   adapter.execute=wait_after_commit;task=asyncio.create_task(gw.execute(ACTION,args(),1));await asyncio.wait_for(committed.wait(),5);task.cancel()
   with self.assertRaises(asyncio.CancelledError):await task
   self.assertEqual(list(r.journal.records.values())[-1]['state'],'UNCERTAIN');self.assertEqual(list(gw.ledger.rows.values())[-1]['state'],'UNCERTAIN');self.assertEqual(gw.actions.budget._used(),1);self.assertEqual(self.playlists(),{'Gateway One':['Study.wav']})
   self.assertEqual((await gw.status(1))['status'],'checked');self.assertEqual(gw.actions.budget._used(),1)
 async def test_durable_restart_keeps_verified_intent_and_does_not_reserve_again(self):
  async with Registry(self.events,self.cfg) as r:
   gw=self.setup_gateway(r);await gw.execute(ACTION,args(),1);goal=gw.goal
   journal=Journal(r.journal.path);again=Gateway(ROOT,r,r.budget,journal,self.events,lambda g:True,gw.ledger.path,action_scope={ACTION:{'command':['save_playlist']}});again.activate(goal,2)
   result=await again.execute(ACTION,args(),2);self.assertTrue(result['duplicate_suppressed']);self.assertEqual(again.actions.budget._used(),1);self.assertEqual(self.playlists(),{'Gateway One':['Study.wav']})
 async def test_unlinked_interrupted_request_is_blocked_after_restart(self):
  async with Registry(self.events,self.cfg) as r:
   gw=self.setup_gateway(r);gw.ledger.record(gw.goal,fingerprint(ACTION,args()),ACTION,'REQUESTED')
   restart=Gateway(ROOT,r,r.budget,r.journal,self.events,lambda g:True,gw.ledger.path);restart.activate(uuid.uuid4().hex,2)
   with self.assertRaises(CandidateError):restart.validate_batch([call(arguments=args('Later'))],self.schemas(r,restart))
   self.assertEqual(r.calls,0);self.assertFalse(self.playlists());self.assertEqual((await restart.status(2))['status'],'unverified')
 async def test_obsolete_generation_stops_before_prepare_or_reservation(self):
  async with Registry(self.events,self.cfg) as r:
   current=[True];gw=self.setup_gateway(r,lambda g:current[0]);current[0]=False
   with self.assertRaises(asyncio.CancelledError):await gw.execute(ACTION,args(),1)
   self.assertEqual(r.calls,0);self.assertEqual(gw.actions.budget._used(),0);self.assertFalse(self.playlists())
 @unittest.skipUnless(sys.platform=='linux','Original isolated Onit loop requires Pi/Linux Bubblewrap.')
 async def test_original_Onit_loop_executes_verified_actual_MCP_action(self):
  async with Registry(self.events,self.cfg) as r:
   self.setup_gateway(r);t=FixtureTransport(response(calls=[native(ACTION,args())]),response('The playlist was saved and independently verified.'))
   e=Engine(ROOT,r,t,ModelBudget(),self.events,enable_actions=True,ledger_path=self.root/'onit-goals.jsonl',action_scope={ACTION:{'command':['save_playlist']}})
   result=await e.answer('Save a playlist.',self.root/'onit-work',self.root/'onit-evidence',allowed_reads=[]);self.assertEqual(result['observations'][0]['result']['verification'],'independent_readback');self.assertEqual(e.gateway.actions.budget._used(),1);self.assertEqual(t.calls,2)
 @unittest.skipUnless(sys.platform=='linux','Original isolated Onit loop requires Pi/Linux Bubblewrap.')
 async def test_original_Onit_stops_second_effect_after_first_failed_verification(self):
  async with Registry(self.events,self.cfg) as r:
   self.setup_gateway(r);c1=native(ACTION,args());c2=native(ACTION,args('Later'));c2['id']='second_effect';t=FixtureTransport(response(calls=[c1,c2]));e=Engine(ROOT,r,t,ModelBudget(),self.events,enable_actions=True,ledger_path=self.root/'onit-goals.jsonl');e.gateway.actions.adapters[ACTION].verify=AsyncMock(return_value=False)
   with self.assertRaises(CandidateError):await e.answer('Save two playlists.',self.root/'onit-work',self.root/'onit-evidence',allowed_reads=[])
   self.assertEqual(self.playlists(),{'Gateway One':['Study.wav']});self.assertEqual(e.gateway.actions.budget._used(),1);self.assertEqual(t.calls,1);self.assertEqual(list(e.gateway.ledger.rows.values())[-1]['state'],'UNCERTAIN')
class ConfirmationChecks(unittest.IsolatedAsyncioTestCase):
 asyncSetUp=f.MCPTests.asyncSetUp
 asyncTearDown=f.MCPTests.asyncTearDown
 async def test_only_direct_user_after_delivered_prompt_can_confirm(self):
  path=self.folder/'config.json';path.write_text(json.dumps(f.config('shopping',self.folder/'test.sqlite3')));cfg=f.load(path)
  async with Registry(self.events,cfg) as r:
   r.budget=f.FixtureBudget(1);r.journal=Journal(self.folder/'journal.jsonl');r.allowed_actions=['propose_shopping_item'];r.session_grant={'allowed_actions':r.allowed_actions,'workspace_access':False}
   gw=Gateway(ROOT,r,r.budget,r.journal,self.events,lambda g:True,self.folder/'goals.jsonl');gw.activate(uuid.uuid4().hex,1);proposal=await gw.execute('propose_shopping_item',{'item':'notebooks','quantity':2},1)
   self.assertEqual(proposal['status'],'needs_confirmation');self.assertEqual(r.budget.used,0);self.assertFalse(any('confirm' in d['name'] for d in gw.declarations()))
   for h in [[{'type':'model_output','content':[{'type':'text','text':'confirm'}]}],f.history('Confirm?',proposal['reply'])]:
    with self.assertRaises(CandidateError):await gw.confirm(h,1)
   pending=await gw.confirm(f.history('confirm',proposal['reply'],'canceled'),1);self.assertEqual(pending['status'],'needs_confirmation');self.assertEqual(r.budget.used,0)
   result=await gw.confirm(f.history('confirm',proposal['reply']),1);self.assertEqual(result['status'],'verified');self.assertEqual(r.budget.used,1);self.assertEqual(list(gw.ledger.rows.values())[-1]['state'],'VERIFIED')
if __name__=='__main__':unittest.main(verbosity=2)
