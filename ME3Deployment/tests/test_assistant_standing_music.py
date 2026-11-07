"""Real local-media standing authority, schema factoring and monotonic accounting."""
import asyncio,itertools,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,AsyncMock
from jsonschema import Draft202012Validator
from me3_assistant.standing_music import StandingMusicBudget,initialize,authorized
from me3_assistant.actions import Actions,Journal
from me3_assistant.registry import Registry
from me3_assistant.router import Router
from me3_assistant.workflow import NAME
from tests import test_assistant_music as music
from tests import test_assistant_mode as f
from tests import test_assistant_operating as operating

def grant(root):return dict(profile='standing',routine_local_music=True,allowed_actions=['propose_music_control'],workspace_access=False,operation_state_path=str(root/'operation-state.json'))

class BudgetTests(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.path=self.root/'standing-music-actions.json';self.grant=grant(self.root);self.base=f.FixtureBudget(0);self.now=1000.;initialize(self.path)
 def tearDown(self):self.tmp.cleanup()
 def budget(self):return StandingMusicBudget(self.base,self.grant,self.path,clock=lambda:self.now)
 def test_restarts_and_repeated_initialization_never_reset_separate_counter(self):
  for i in range(3):self.assertEqual(self.budget().reserve('music_control'),i+1)
  initialize(self.path);self.assertEqual(self.budget()._used(),3);self.assertEqual(self.base.used,0)
 def test_rate_expires_but_total_never_decreases_or_spends_other_roles(self):
  b=self.budget()
  for i in range(30):b.reserve('music_control')
  self.assertFalse(b.permitted('music_control'))
  with self.assertRaises(ValueError):b.reserve('music_control')
  self.now+=61;self.assertEqual(self.budget().reserve('music_control'),31);self.assertFalse(b.permitted('commit_item'));self.assertFalse(b.permitted('reminder_control'));self.assertEqual(self.base.used,0)
 def test_missing_counter_decrease_changed_scope_and_invalid_clock_fail_closed(self):
  b=self.budget();b.reserve('music_control');data=json.loads(self.path.read_bytes());data['music_control']=0;self.path.write_text(json.dumps(data),encoding='utf-8')
  with self.assertRaises(ValueError):b._used()
  for values in [dict(profile='qualification'),dict(workspace_access=True),dict(allowed_actions=['propose_shopping_item']),dict(routine_local_music=False)]:self.assertFalse(authorized({**self.grant,**values}))
  self.now=float('nan')
  with self.assertRaises(ValueError):self.budget().permitted('music_control')
  self.path.unlink()
  with self.assertRaises(ValueError):self.budget()._used()

class AuthorizationTests(unittest.TestCase):
 setUp=operating.OperatingTests.setUp
 tearDown=operating.OperatingTests.tearDown
 write=operating.OperatingTests.write
 def test_explicit_music_policy_requires_provisioned_counter_and_denies_other_actions(self):
  from me3_assistant.operating import authorize_operating,digest
  self.cfg['actions']=[dict(name='propose_music_control',adapter='music_control')];self.policy.update(allowed_actions=['propose_music_control'],routine_local_music=True)
  resource=self.root/'config/operating_policy.json';self.write(resource,self.policy);self.src['source_hashes']['config/operating_policy.json']=digest(resource);self.authority['resource_policy_sha256']=digest(resource);self.write(self.auth,self.authority)
  state=json.loads(self.state.read_bytes());state['policy_sha256']=digest(self.auth);self.write(self.state,state)
  phase=self.root/'evidence/completion/autonomous-20261020-001';phase.mkdir(parents=True);self.write(phase/'budget.json',self.usage)
  with self.assertRaises(ValueError):authorize_operating(self.cfg,self.src,'normal',root=self.root)
  initialize(self.root/'evidence/assistant/standing-music-actions.json');g,_=authorize_operating(self.cfg,self.src,'normal',root=self.root);self.assertTrue(authorized(g));self.assertEqual(g['allowed_actions'],['propose_music_control'])

class ActualMCPTests(unittest.IsolatedAsyncioTestCase):
 asyncSetUp=music.MusicMCPTests.asyncSetUp
 asyncTearDown=music.MusicMCPTests.asyncTearDown
 def setup(self,r):
  r.session_grant=grant(self.root);r.standing_music_counter=self.root/'standing-music-actions.json';initialize(r.standing_music_counter);r.budget=f.FixtureBudget(0);r.journal=Journal(self.root/'standing-journal.jsonl');r.allowed_actions=['propose_music_control'];return r
 async def test_routine_playlist_is_saved_and_verified_without_permission_or_model_followup(self):
  async with Registry(self.events,self.cfg) as r:
   self.setup(r);model=f.Model(f.decision(NAME,{'steps':[{'tool':'propose_music_control','arguments':{'command':'save_playlist','playlist_name':'First','track_ids':['Study.wav']}},{'tool':'propose_music_control','arguments':{'command':'save_playlist','playlist_name':'Second','track_ids':['Study.wav']}}]}));router=Router(model,r,self.events)
   reply=await router.answer(f.history('Save two study playlists using my local track.'));self.assertNotIn('confirm',reply.lower());self.assertIsNone(router.pending);self.assertEqual(router.workflow.states,['verified','verified']);self.assertEqual(len(model.seen),1);self.assertEqual(router.actions.budget._used(),2);self.assertEqual(r.budget.used,0)
   self.assertFalse(any(e['event']=='assistant_human_confirmation' for e in self.events.rows));self.assertEqual(sum(e['event']=='assistant_standing_music_authorized' for e in self.events.rows),2)
 async def test_uncertain_committed_routine_action_is_not_retried(self):
  async with Registry(self.events,self.cfg) as r:
   self.setup(r);a=Actions(r,r.config['actions'],r.budget,r.journal,self.events,lambda g:True,allowed=r.allowed_actions);a.adapters['propose_music_control'].verify=AsyncMock(return_value=False)
   reply=await a.propose('propose_music_control',dict(command='save_playlist',playlist_name='One',track_ids=['Study.wav']),1);self.assertIn('not repeat',reply);before=dict(a.journal.records)
   await a.propose('propose_music_control',dict(command='save_playlist',playlist_name='Two',track_ids=['Study.wav']),1);self.assertEqual(a.journal.records,before);self.assertEqual(a.budget._used(),1)
   data=json.loads((self.media/'.me3-playlists.json').read_bytes());self.assertEqual(set(data['playlists']),{'One'})
 async def test_compact_schema_preserves_command_field_and_value_guards(self):
  async with Registry(self.events,self.cfg) as r:
   self.setup(r);a=Actions(r,r.config['actions'],r.budget,r.journal,self.events,lambda g:True,allowed=r.allowed_actions);adapter=a.adapters['propose_music_control']
   with patch('me3_assistant.standing_music.compact_parameters',side_effect=lambda schema,branches:{**schema,'anyOf':branches}):old=adapter.declaration()['parameters']
   compact=adapter.declaration()['parameters'];self.assertLess(len(json.dumps(compact)),len(json.dumps(old)))
   fields={'track_id':'Study.wav','volume':20,'track_ids':['Study.wav'],'playlist_name':'Study'};before=Draft202012Validator(old);after=Draft202012Validator(compact)
   for command in ['play','pause','resume','stop','volume','enqueue','replace_queue','clear_queue','skip','save_playlist','play_playlist','delete']:
    for size in range(5):
     for keys in itertools.combinations(fields,size):
      args={'command':command,**{k:fields[k] for k in keys}};self.assertEqual(before.is_valid(args),after.is_valid(args),args)
   for args in [dict(command='play',track_id='../outside.mp3'),dict(command='volume',volume=101),dict(command='volume',volume=True),dict(command='play',track_id=None),dict(command='enqueue',track_ids=[]),dict(command='skip',operation_id='injected')]:self.assertFalse(after.is_valid(args),args)

if __name__=='__main__':unittest.main()
