"""Synthetic state-transition regressions; these are not acoustic trials."""
import asyncio,base64,unittest
from types import SimpleNamespace
from unittest.mock import patch,AsyncMock
from pipecat.frames.frames import InputAudioRawFrame,InterruptionFrame
from pipecat.processors.frame_processor import FrameProcessor,FrameDirection
from pipecat.audio.vad.vad_analyzer import VADState
from duplex_audio import PlayoutBuffer
from me3_completion.pipeline import build_pipeline
from voice_agent import Settings
from tests.test_completion import Events
class VAD:
    def __init__(self,**kw):self.state=VADState.QUIET
    def set_sample_rate(self,*args):pass
    async def analyze_audio(self,pcm):return self.state
    async def cleanup(self):pass
class Endpoint:
    def __init__(self,*args):self.complete=False;self.calls=[];self.closed=False
    async def decide(self,pcm,token):self.calls.append((pcm,token));return self.complete
    async def close(self):self.closed=True
class Transport:
    def __init__(self):self.state=SimpleNamespace(buffer=PlayoutBuffer(5760),capture_count=10);self.a=FrameProcessor();self.b=FrameProcessor()
    def input(self):return self.a
    def output(self):return self.b
class Clients:
    def __init__(self):self.calls=[];self.answer='What is the capital of France?';self.block=None
    async def call(self,role,g,**args):
        self.calls.append((role,g,args))
        if self.block is not None and role=='asr':await self.block.wait()
        if role=='asr':return {'text':self.answer,'generation':g,'request_id':'asr-synthetic'}
        return {'pcm_b64':base64.b64encode(bytes(9600)).decode()}
class Reasoner:
    pending=None
    async def answer(self,history):return 'Paris.'
class PipelineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.events=Events();self.clients=Clients();transport=Transport()
        with patch('duplex_audio.make_duplex_transport',return_value=(transport,transport.state)),patch('pipecat.audio.vad.silero.SileroVADAnalyzer',VAD),patch('me3_completion.pipeline.LocalEndpoint',Endpoint):
            self.runner,self.worker,self.transport,self.p=build_pipeline(Settings(api_key=''),self.events,self.clients,Reasoner())
        self.frames=[]
        async def push(frame,*args):self.frames.append(frame)
        self.p.push_frame=push;self.played=[]
        async def play(pcm,g):self.played.append(g)
        self.p.controller.play_audio=play
    async def asyncTearDown(self):await self.p.cleanup()
    async def feed(self,state,count=1):
        self.p.vad.state=state
        for _ in range(count):await self.p.process_frame(InputAudioRawFrame(audio=b'\1\0'*320,sample_rate=16000,num_channels=1),FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0)
    async def settle(self,predicate):
        async with asyncio.timeout(2):
            while not predicate():await asyncio.sleep(.001)
    async def test_incomplete_pause_keeps_audio_and_sends_one_combined_asr_after_continuation(self):
        await self.feed(VADState.STARTING,5);await self.feed(VADState.SPEAKING,25);await self.feed(VADState.STOPPING,35);await self.feed(VADState.QUIET)
        await self.settle(lambda:any(x['event']=='completion_turn_held' for x in self.events.rows));first=len(self.p.pcm)
        self.assertFalse(any(x[0]=='asr' for x in self.clients.calls))
        await self.feed(VADState.QUIET,40);await self.feed(VADState.STARTING,5);await self.feed(VADState.SPEAKING,15)
        self.p.endpoint.complete=True;await self.feed(VADState.STOPPING,35);await self.feed(VADState.QUIET)
        await self.settle(lambda:self.p.controller.task is not None and self.p.controller.task.done())
        asr=[x for x in self.clients.calls if x[0]=='asr'];self.assertEqual(len(asr),1);self.assertGreater(len(base64.b64decode(asr[0][2]['pcm_b64'])),first);self.assertEqual(self.played,[1])
    async def test_user_backchannel_resumes_only_suspended_uncanceled_response(self):
        async def old():await asyncio.Event().wait()
        self.p.controller.spawn(old());task=self.p.controller.task;await asyncio.sleep(0)
        self.p.output_task=task;self.p.output_generation=0;self.p.output_kind='response';self.clients.answer='Mm-hmm.'
        await self.feed(VADState.STARTING,5);await self.feed(VADState.SPEAKING,12);self.assertFalse(self.p.gate.ready.is_set())
        await self.feed(VADState.STOPPING,35);await self.feed(VADState.QUIET)
        await self.settle(lambda:any(x['event']=='completion_user_backchannel' for x in self.events.rows))
        self.assertEqual(self.p.controller.clock.current,0);self.assertFalse(task.done());self.assertTrue(self.p.gate.ready.is_set());self.assertEqual(self.p.controller.turns,0);self.assertEqual(self.p.controller.listener_ack_count,1)
        task.cancel();await asyncio.gather(task,return_exceptions=True)
    async def test_short_stop_cancels_old_response_and_reuses_probe_for_command(self):
        async def old():await asyncio.Event().wait()
        self.p.controller.spawn(old());task=self.p.controller.task;await asyncio.sleep(0)
        self.p.output_task=task;self.p.output_generation=0;self.p.output_kind='response';self.clients.answer='Stop.'
        await self.feed(VADState.STARTING,5);await self.feed(VADState.SPEAKING,12);await self.feed(VADState.STOPPING,35);await self.feed(VADState.QUIET)
        await self.settle(lambda:self.p.controller.clock.current==1 and self.p.controller.task.done())
        self.assertTrue(task.cancelled());self.assertEqual(len([x for x in self.clients.calls if x[0]=='asr']),1);self.assertTrue(any(isinstance(f,InterruptionFrame) for f in self.frames));self.assertEqual(self.played,[1])
    async def test_long_overlap_cancels_before_any_asr_dispatch(self):
        async def old():await asyncio.Event().wait()
        self.p.controller.spawn(old());task=self.p.controller.task;await asyncio.sleep(0)
        self.p.output_task=task;self.p.output_generation=0;self.p.output_kind='response'
        await self.feed(VADState.STARTING,5);await self.feed(VADState.SPEAKING,60)
        await asyncio.sleep(0);self.assertEqual(self.p.controller.clock.current,1);self.assertTrue(task.cancelled());self.assertFalse(self.clients.calls);self.assertTrue(self.p.gate.ready.is_set())
    async def test_stale_overlap_result_cannot_resume_or_dispatch_reasoning(self):
        self.clients.block=asyncio.Event();self.p.pcm=bytearray(bytes(6400));self.p.gate.suspend()
        work=self.p.spawn(self.p.classify_overlap(0,bytes(6400),.4,0,None));await asyncio.sleep(0);self.p.token=1;self.clients.block.set();await work
        self.assertEqual(self.p.controller.clock.current,0);self.assertFalse(self.p.gate.ready.is_set());self.assertFalse(self.played);self.assertTrue(any(x['event']=='completion_overlap_result_stale' for x in self.events.rows))
    async def test_held_turn_backchannel_yields_without_losing_user_pcm(self):
        await self.feed(VADState.STARTING,5);await self.feed(VADState.SPEAKING,90);await self.feed(VADState.STOPPING,35);await self.feed(VADState.QUIET)
        await self.settle(lambda:any(x['event']=='completion_agent_backchannel' for x in self.events.rows));before=len(self.p.pcm)
        await self.feed(VADState.STARTING,5);await self.feed(VADState.SPEAKING,5)
        self.assertGreater(len(self.p.pcm),before);self.assertTrue(self.p.cue.cancelled() or self.p.cue.cancelling());self.assertTrue(any(x['event']=='completion_continuation_captured' for x in self.events.rows));self.assertFalse(any(x[0]=='asr' for x in self.clients.calls))
    async def test_overlong_audio_is_rejected_without_partial_action(self):
        await self.feed(VADState.SPEAKING,760);await self.feed(VADState.QUIET)
        await self.settle(lambda:self.p.controller.task is not None and self.p.controller.task.done())
        self.assertTrue(any(x['event']=='utterance_limit_reached' for x in self.events.rows));self.assertFalse(any(x[0]=='asr' for x in self.clients.calls));self.assertEqual(self.played,[1])

    async def test_starting_pulse_defers_commit_and_quiet_reschedules_without_lost_speech(self):
        await self.feed(VADState.STARTING,5);await self.feed(VADState.SPEAKING,20);await self.feed(VADState.STOPPING,35);await self.feed(VADState.QUIET)
        await self.settle(lambda:any(x['event']=='completion_turn_held' for x in self.events.rows))
        token=self.p.token;await self.feed(VADState.STARTING)
        self.assertFalse(self.p.valid(token));self.assertFalse(any(x[0]=='asr' for x in self.clients.calls))
        self.p.endpoint.complete=True;await self.feed(VADState.QUIET)
        await self.settle(lambda:self.p.controller.task is not None and self.p.controller.task.done())
        self.assertEqual(len([x for x in self.clients.calls if x[0]=='asr']),1)
