"""Offline standing-policy admission, restart and crash-accounting checks."""
import hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from me3_assistant.operating import OperatingBudget,authorize_operating,digest
from speech_mcp.client import ProviderLimited

class OperatingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.phase=self.root/'phase';self.phase.mkdir()
        self.usage=dict(asr=214,reasoning=234,tts=360,commit_item=2);self.now=1000.0
        p=self.root/'config/operating_policy.json';p.parent.mkdir()
        self.cfg={'sha256':'cfg','runtime':{},'approved_modules':[]};self.src={'revision':'src','source_hashes':{}}
        self.policy=dict(approved=True,modes=['normal','text'],source_revision='src',config_sha256='cfg',runtime={},allowed_actions=[],workspace_access=False,backend='groq',audio_mode='earphones',hosted_requests_per_minute=14,max_input_seconds=120)
        self.write(p,self.policy);self.src['source_hashes']['config/operating_policy.json']=digest(p)
        self.auth=self.root/'evidence/assistant/operating-authorization.json';self.auth.parent.mkdir(parents=True)
        self.authority=dict(approved=True,source_revision='src',config_sha256='cfg',resource_policy_sha256=digest(p))
        self.write(self.auth,self.authority);self.write(self.phase/'budget.json',self.usage)
        self.state=self.root/'evidence/assistant/operation-state.json';self.state.parent.mkdir(parents=True,exist_ok=True)
        self.write(self.state,dict(policy_sha256=digest(self.auth),baseline=self.usage,usage=self.usage,hosted_attempts=[],pending=None))
        self.grant,self.limits=authorize_operating(self.cfg,self.src,'normal',root=self.root)
    def tearDown(self):self.tmp.cleanup()
    def write(self,p,obj):p.write_text(json.dumps(obj),encoding='utf-8')
    def budget(self,grant=None):return OperatingBudget(grant or self.grant,self.limits,self.phase,clock=lambda:self.now)
    def test_local_speech_continues_beyond_old_ceiling_and_restart_keeps_counter(self):
        b=self.budget();self.assertEqual(b.reserve('tts'),361)
        for _ in range(20):self.budget().reserve('tts')
        self.assertEqual(json.loads((self.phase/'budget.json').read_bytes())['tts'],381)
        self.assertEqual(json.loads(self.state.read_bytes())['baseline'],self.usage)
        self.assertGreater(b.remaining('tts'),1000)
    def test_rate_survives_restart_then_expires_without_resetting_counters(self):
        for _ in range(14):self.budget().reserve('reasoning')
        with self.assertRaises(ProviderLimited):self.budget().reserve('asr')
        self.assertEqual(json.loads((self.phase/'budget.json').read_bytes())['reasoning'],248)
        self.now+=61;self.assertEqual(self.budget().reserve('asr'),215)
        self.assertEqual(json.loads((self.phase/'budget.json').read_bytes())['reasoning'],248)
    def test_revocation_and_source_config_changes_fail_before_reservation(self):
        for key,value in [('approved',False),('source_revision','changed'),('config_sha256','changed'),('allowed_actions',['write'])]:
            with self.subTest(key=key):
                self.write(self.root/'config/operating_policy.json',{**self.policy,key:value})
                with self.assertRaises(ValueError):authorize_operating(self.cfg,self.src,'normal',root=self.root)
        with self.assertRaises(ValueError):self.budget().reserve('tts')
        self.assertEqual(json.loads((self.phase/'budget.json').read_bytes()),self.usage)
    def test_pending_reservation_is_charged_once_on_both_sides_of_mirror_write(self):
        for mirrored in [False,True]:
            after={**self.usage,'asr':215}
            self.write(self.state,dict(policy_sha256=self.grant['policy_sha256'],baseline=self.usage,usage=self.usage,hosted_attempts=[self.now],pending=dict(before=self.usage,after=after,role='asr')))
            self.write(self.phase/'budget.json',after if mirrored else self.usage)
            b=self.budget();self.assertEqual(b.remaining('asr'),13)
            self.assertEqual(json.loads((self.phase/'budget.json').read_bytes()),after)
            self.assertEqual(self.budget().remaining('asr'),13)
    def test_decreased_or_conflicting_counter_is_rejected(self):
        for count in [213,215]:
            self.write(self.phase/'budget.json',{**self.usage,'asr':count})
            with self.assertRaises(ValueError):self.budget().reserve('tts')
    def test_typed_profile_denies_speech_and_all_profiles_deny_task_writes(self):
        text,_=authorize_operating(self.cfg,self.src,'text',root=self.root)
        for role in ['tts','asr','commit_item']:
            with self.assertRaises(ProviderLimited):self.budget(text).reserve(role)
        with self.assertRaises(ProviderLimited):self.budget().reserve('commit_item')
        self.assertEqual(self.budget(text).reserve('reasoning'),235)
    def test_changed_policy_and_backwards_clock_deny_dispatch(self):
        self.budget().reserve('asr');self.now-=2
        with self.assertRaises(ValueError):self.budget().reserve('reasoning')
        self.now+=2;self.write(self.auth,{**self.authority,'approved':False})
        with self.assertRaises(ValueError):self.budget().reserve('tts')
    def test_shared_budget_factory_uses_standing_path_only_for_explicit_profile(self):
        from me3_assistant.guard import SharedBudget
        self.assertIsInstance(SharedBudget(self.grant,self.limits,self.phase),OperatingBudget)
        self.assertNotIsInstance(SharedBudget({},dict(asr=1),self.phase),OperatingBudget)
    def test_board_accepts_growing_standing_counters_but_not_unmarked_uncapped_budget(self):
        from tools.project_workflow import validate,render
        from task_mcp.client import ROOT
        board=json.loads((ROOT/'PROJECT_BOARD.json').read_bytes())
        group=board['budget_snapshot']['development']
        group.update(profile='standing',usage=dict(asr=999,reasoning=999,tts=999,commit_item=2),limits=dict(asr=None,reasoning=None,tts=None,commit_item=2))
        validate(board,ROOT,check_evidence=False);self.assertIn('Rolling/provider controls',render(board))
        group['profile']='qualification'
        with self.assertRaises(AssertionError):validate(board,ROOT,check_evidence=False)

if __name__=='__main__':unittest.main()
