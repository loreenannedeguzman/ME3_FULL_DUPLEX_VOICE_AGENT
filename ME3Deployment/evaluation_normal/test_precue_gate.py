"""Voiced input while waiting must not initiate recognition; active input unchanged."""
import asyncio,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from evaluation_normal import supplement as s
from pipecat.frames.frames import InputAudioRawFrame,StartFrame,EndFrame,CancelFrame
from pipecat.processors.frame_processor import FrameDirection

class Sink:
    def __init__(self):self.active=None;self.rows=[]
    def emit(self,event,**fields):self.rows.append({'event':event,**fields})

class InputGateTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self):
        events=Sink();seen=[];calls=[]
        async def process(frame,direction):
            seen.append((frame,direction))
            if isinstance(frame,InputAudioRawFrame) and direction==FrameDirection.DOWNSTREAM:calls.append('would_dispatch_ASR')
        processor=SimpleNamespace(process_frame=process)
        # Exact previous supplement behavior: input processor receives every frame.
        getattr(s,'gate_uncued_input',lambda p,e:p)(processor,events)
        return processor,events,seen,calls
    def audio(self):return InputAudioRawFrame(audio=b'\xff\x3f'*320,sample_rate=16000,num_channels=1)
    async def test_voiced_input_cannot_spend_ASR_before_first_cue(self):
        p,e,seen,calls=self.fixture()
        for _ in range(8):await p.process_frame(self.audio(),FrameDirection.DOWNSTREAM)
        self.assertEqual(calls,[]);self.assertEqual(seen,[])
    async def test_cued_audio_pauses_and_interruptions_are_not_changed(self):
        p,e,seen,calls=self.fixture();e.active='F01'
        frames=[self.audio(),InputAudioRawFrame(audio=bytes(640),sample_rate=16000,num_channels=1),self.audio()]
        for f in frames:await p.process_frame(f,FrameDirection.DOWNSTREAM)
        self.assertEqual([f for f,d in seen],frames);self.assertEqual(len(calls),3)
    async def test_intertrial_and_after_final_report_input_are_held_locally(self):
        p,e,seen,calls=self.fixture()
        for trial in ['F01',None,'F02',None]:
            e.active=trial;await p.process_frame(self.audio(),FrameDirection.DOWNSTREAM)
        self.assertEqual(len(calls),2)
    async def test_startup_shutdown_and_upstream_frames_still_flow(self):
        p,e,seen,calls=self.fixture();frames=[StartFrame(),EndFrame(),CancelFrame()]
        for f in frames:await p.process_frame(f,FrameDirection.DOWNSTREAM)
        up=self.audio();await p.process_frame(up,FrameDirection.UPSTREAM)
        self.assertEqual([x[0] for x in seen],frames+[up]);self.assertEqual(calls,[])
    async def test_gate_metadata_never_carries_audio_or_text(self):
        p,e,seen,calls=self.fixture()
        for trial in [None,None,'F01','F01',None]:
            e.active=trial;await p.process_frame(self.audio(),FrameDirection.DOWNSTREAM)
        self.assertTrue(e.rows)
        self.assertTrue(all(set(x)<= {'event','state','reason'} for x in e.rows))

class ReportSettlementTests(unittest.TestCase):
    def test_success_cannot_close_trial_while_another_input_is_buffered(self):
        from evaluation_normal.test_supplement import SelectedEventsTests
        with tempfile.TemporaryDirectory() as tmp:
            e=SelectedEventsTests().fixture(tmp)
            try:
                e.command({'action':'cue','trial':'F01'});e.processor.pcm=b'input not yet settled'
                with self.assertRaisesRegex(ValueError,'settled'):e.command(SelectedEventsTests().report('F01'))
                self.assertEqual(e.active,'F01');self.assertEqual(e.finished,[])
                # An operator-reported failure must still stop immediately.
                e.command(SelectedEventsTests().report('F01','fail'));self.assertTrue(e.processor.pipeline_failed.is_set())
            finally:e.close()
if __name__=='__main__':unittest.main()
