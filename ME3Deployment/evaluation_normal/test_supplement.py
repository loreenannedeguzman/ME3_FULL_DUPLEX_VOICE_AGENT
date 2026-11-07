"""Provider-free supplement boundaries. These are not live acceptance results."""
import asyncio,copy,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from evaluation_normal import supplement as s
from evaluation_normal.adapter import protocol

class SupplementTests(unittest.TestCase):
    def parent(self):
        plan,methods=protocol();trials=[dict(t,status='UNSTARTED' if t['id'] in s.SELECTED else 'FAIL' if t['id']=='F00' else 'PASS') for ts in plan.values() for t in ts]
        return ({'source_revision':s.SOURCE,'protocol':plan,'methods':methods},{'continuation_prohibited':True,'trials':trials},{'source_revision':s.SOURCE,'operator_failed':1},{'no_further_calls':True})
    def test_only_preserved_unstarted_tail_is_eligible(self):
        p=self.parent();before=copy.deepcopy(p);s.validate_parent(*p,s.SELECTED);self.assertEqual(p,before)
        for chosen in (['F00'],['F02','F01'],['F01'],['A01','F02']):
            with self.subTest(chosen=chosen),self.assertRaises(ValueError):s.validate_parent(*p,chosen)
    def test_failures_unstarted_identities_and_hold_cannot_be_rewritten(self):
        for change in ('failure','unstarted','hold','identity','source','script'):
            p=list(self.parent())
            if change=='failure':next(t for t in p[1]['trials'] if t['id']=='F00')['status']='PASS'
            elif change=='unstarted':p[1]['trials'][-1]['status']='PASS'
            elif change=='hold':p[3]['no_further_calls']=False
            elif change=='identity':p[1]['trials'][0]['id']='F01'
            elif change=='source':p[0]['source_revision']='changed'
            else:p[0]['protocol']['F'][-1]['script']=['write something']
            with self.subTest(change=change),self.assertRaises(ValueError):s.validate_parent(*p,s.SELECTED)
    def allocation(self):
        start={'asr':79,'reasoning':100,'tts':110,'commit_item':0};limits={'asr':82,'reasoning':103,'tts':131,'commit_item':0}
        return {'approved':True,'budget_scope':'benchmark_normal','no_additional_writes':True,'aggregate_start':start.copy(),'limits':limits.copy(),'new_reservation_ceiling':{k:limits[k]-start[k] for k in start}},{'limits':limits},start
    def test_held_allocation_is_carried_not_reset_increased_or_transferred(self):
        a,p,u=self.allocation();before=copy.deepcopy((a,p,u));s.validate_allocation(a,p,u);self.assertEqual((a,p,u),before)
        for change in ('increase','reset','write','development','unapproved','extra'):
            a,p,u=self.allocation()
            if change=='increase':a['limits']['asr']+=1;a['new_reservation_ceiling']['asr']+=1
            elif change=='reset':a['aggregate_start']['asr']=0
            elif change=='write':a['limits']['commit_item']=1;a['new_reservation_ceiling']['commit_item']=1
            elif change=='development':a['budget_scope']='development'
            elif change=='unapproved':a['approved']=False
            else:a['new_reservation_ceiling']['asr']+=1
            with self.subTest(change=change),self.assertRaises(ValueError):s.validate_allocation(a,p,u)
    def test_missing_manifest_cannot_start_or_create_runtime_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);base=root/'evidence/benchmark_normal'
            with self.assertRaises(FileNotFoundError):s.verify(root,base/'supplement',base/'accounting-new',base/'grant.json')
            self.assertFalse(base.exists())
    def test_only_explicit_amendment_can_restore_spent_ASR_and_reasoning(self):
        a,p,u=self.allocation();u.update(asr=82,reasoning=102,tts=114);a.update(aggregate_start=u.copy(),limits={'asr':85,'reasoning':105,'tts':131,'commit_item':0},new_reservation_ceiling={'asr':3,'reasoning':3,'tts':17,'commit_item':0})
        with self.assertRaises(ValueError):s.validate_allocation(a,p,u)
        a['authorization_increment']={'asr':3,'reasoning':2,'tts':0,'commit_item':0};s.validate_allocation(a,p,u)
        a['authorization_increment']['commit_item']=1
        with self.assertRaises(ValueError):s.validate_allocation(a,p,u)
    def test_development_paths_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with self.assertRaises(ValueError):s.verify(root,root/'evidence/completion/dev',root/'phase',root/'grant')

class SelectedEventsTests(unittest.TestCase):
    def fixture(self,folder):
        from benchmark_final.runner import Events
        class Selected(s.SelectionGuard,Events):pass
        e=Selected(Path(folder)/'F','F');e.processor=SimpleNamespace(controller=SimpleNamespace(task=None),pcm=b'',speaking=False,jobs=[],pipeline_failed=asyncio.Event())
        return e
    def report(self,trial,outcome='pass'):
        return {'action':'report','trial':trial,'outcome':outcome,'audible':True,'response_correct':outcome=='pass','note':'none'}
    def test_actual_event_controller_enforces_selection_order_no_repeat_and_settlement(self):
        with tempfile.TemporaryDirectory() as tmp:
            e=self.fixture(tmp)
            try:
                for t in ('F00','F02','A01'):
                    with self.assertRaises(ValueError):e.command({'action':'cue','trial':t})
                e.command({'action':'cue','trial':'F01'})
                with self.assertRaises(ValueError):e.command({'action':'cue','trial':'F01'})
                e.command(self.report('F01'));e.last_cue_ns-=11_000_000_000
                with self.assertRaises(ValueError):e.command(self.report('F01'))
                e.command({'action':'cue','trial':'F02'});e.command(self.report('F02'))
                self.assertEqual(e.finished,['F01','F02']);self.assertFalse(e.failed)
                with self.assertRaises(ValueError):e.command({'action':'cue','trial':'F01'})
            finally:e.close()
    def test_reported_failure_signals_shutdown_and_blocks_next_cue(self):
        with tempfile.TemporaryDirectory() as tmp:
            e=self.fixture(tmp)
            try:
                e.command({'action':'cue','trial':'F01'});e.command(self.report('F01','fail'))
                self.assertTrue(e.failed);self.assertTrue(e.processor.pipeline_failed.is_set())
                with self.assertRaises(ValueError):e.command({'action':'cue','trial':'F02'})
            finally:e.close()
if __name__=='__main__':unittest.main()
