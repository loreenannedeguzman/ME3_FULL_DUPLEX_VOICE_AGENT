import array,asyncio,math,random,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from me3_assistant.acoustic_echo import Reference
from me3_assistant.gui import Console
from tests.test_assistant_mode import config

def legacy_frame(chunks,start):
    result=array.array('h',[0])*320;covered=0
    for stamp,values in chunks:
        begin=max(0,math.ceil((stamp-start)*16000-.01));end=min(320,math.ceil((stamp-start)*16000+len(values)-.01))
        for i in range(begin,end):
            j=round((start+i/16000-stamp)*16000)
            if 0<=j<len(values):result[i]=values[j];covered+=1
    return result.tobytes(),min(320,covered)

class ReferenceEquivalenceTests(unittest.TestCase):
    def test_exact_PCM_and_coverage_match_legacy_across_jitter_gaps_overlap_and_long_clocks(self):
        randomizer=random.Random(72)
        for base in [1.,494000.,10000000.]:
            r=Reference()
            for i in range(64):r.chunks.append((base+i*.02+randomizer.choice([0,0,.0001,-.0001]),array.array('h',[randomizer.randrange(-20000,20000) for _ in range(320)])))
            for offset in [-.02,0,.003,.01,.019,1.,1.259,1.28]:
                for phase in [0,.49999,.5,.50001,.8]:
                    stamp=base+offset+phase/16000
                    self.assertEqual(r.frame(stamp),legacy_frame(tuple(r.chunks),stamp),(base,offset,phase))

class GateDisplayCostTests(unittest.IsolatedAsyncioTestCase):
    def console(self):
        cfg=config();cfg['sha256']='fixture';return Console(cfg,{'revision':'fixture'})
    async def test_cached_display_never_bypasses_fresh_start_authorization(self):
        c=self.console()
        with patch.object(c,'gate',return_value=({'backend':'groq'},{})) as gate:
            for i in range(8):await asyncio.to_thread(c.status_checks)
            self.assertEqual(gate.call_count,2)
        with patch.object(c,'gate',side_effect=ValueError('revoked')) as gate:
            with self.assertRaisesRegex(ValueError,'revoked'):await c.command({'action':'start_voice'})
            gate.assert_called_once();self.assertIsNone(c.task)
    async def test_approval_file_change_invalidates_display_without_waiting_TTL(self):
        with tempfile.TemporaryDirectory() as d,patch('me3_assistant.gui.NORMAL',Path(d)):
            p=Path(d)/'operating-authorization.json';p.write_text('approved');c=self.console()
            with patch.object(c,'gate',return_value=({'backend':'groq'},{})):self.assertTrue(c.status_checks()[2]['normal']['ready'])
            p.write_text('revoked and changed')
            with patch.object(c,'gate',side_effect=ValueError('revoked')):self.assertFalse(c.status_checks()[2]['normal']['ready'])
    async def test_audio_failure_is_not_misreported_as_a_network_problem(self):
        c=self.console()
        async def fail(*a,**kw):kw['observer']('speaker_aec_fault',stage='capture',code='speaker_aec_capture_discontinuity');raise RuntimeError('fixture')
        with tempfile.TemporaryDirectory() as d,patch('me3_assistant.gui.NORMAL',Path(d)),patch('me3_assistant.runner.run',fail):await c.voice()
        self.assertIn('audio',c.public_error);self.assertNotIn('connection',c.public_error);self.assertEqual(c.state,'stopped')
