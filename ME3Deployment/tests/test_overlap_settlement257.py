"""Positive checks of the separate lifecycle overlay, no model/audio/provider."""
import ast,asyncio,base64,copy,os,runpy,time,types,unittest
from pathlib import Path
H=Path(__file__).resolve().parent
F=runpy.run_path(str(H/'overlap_fixture257.py'))
STAGE=Path(os.environ.get('ME3_LATENCY_STAGE',str(F['ROOT'])))
class Recoverable(Exception):pass

async def fixture(*,text='okay',asr_error=False,deadline=None):
    f=await F['Fixture']().open();p=f.p
    ns=dict(FrameProcessor=F['Base'],FrameDirection=types.SimpleNamespace(UPSTREAM='up'),InputAudioRawFrame=F['Input'],
      StartFrame=F['Start'],CancelFrame=F['Cancel'],EndFrame=F['End'],VADState=F['VAD'],events=f.events,time=time,
      state=p.state,gate=p.gate,provider=f.provider,Ownership=types.SimpleNamespace(USER='user',SUSPENDED='suspended',CLOSED='closed'))
    source=ast.parse((STAGE/'me3_completion/pipeline.py').read_text())
    cls=next(n for n in ast.walk(source) if isinstance(n,ast.ClassDef) and n.name=='ConversationProcessor')
    methods=[copy.deepcopy(n) for n in cls.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in {'valid','overlap_review_identity','wait_overlap_onset_settled','process_frame','shutdown'}]
    node=ast.ClassDef(name='CandidateProcessor',bases=[ast.Name(id='FrameProcessor',ctx=ast.Load())],keywords=[],body=methods,decorator_list=[])
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),'actual_lifecycle_overlay','exec'),ns)
    p.__class__=ns['CandidateProcessor'];p.overlap_onset_settled=asyncio.Event();p.overlap_onset_settled.set()
    async def closed():pass
    p.controller.close=closed;p.endpoint=types.SimpleNamespace(close=closed)
    async def call(role,g,**args):
        f.calls.append((role,g,args));f.entered.set();await f.release.wait()
        if asr_error:raise Recoverable()
        return dict(text=text,generation=g,request_id='asr-fixture-exact')
    policy=dict(re=__import__('re'),math=__import__('math'))
    tree=ast.parse((F['ROOT']/'me3_completion/policy.py').read_text())
    exec(compile(ast.Module(body=[n for n in tree.body if not isinstance(n,(ast.Import,ast.ImportFrom))],type_ignores=[]),'policy.py','exec'),policy)
    scope=dict(asyncio=asyncio if deadline is None else types.SimpleNamespace(timeout=lambda ignored:asyncio.timeout(deadline),CancelledError=asyncio.CancelledError),
      base64=base64,Ownership=types.SimpleNamespace(RESPONSE='response',PROCESSING='processing'),
      annotation=policy['annotation'],listener_ack=policy['listener_ack'],RecoverableASRTimeout=Recoverable)
    F['selected'](STAGE/'me3_agent/overlap.py',{'attach'},scope)['attach'](p,types.SimpleNamespace(call=call),f.provider,f.events)
    return f
async def deferred(f):
    await f.feed(F['VAD'].STARTING);f.release.set()
    for _ in range(10):
        await asyncio.sleep(0)
        if any(r['event']=='completion_overlap_onset_deferred' for r in f.events.rows):break
    if f.p.probe.done():raise AssertionError('completed_result_was_not_deferred')

class CandidateSettlement(unittest.TestCase):
    def case(self,fn,**kw):
        async def run():
            f=await fixture(**kw)
            try:await fn(f)
            finally:await f.close()
        F['LOOP']['run_audio_fixture'](run())
    def test_failed_STARTING_pulse_settles_exact_result_and_resumes_once(self):
        async def check(f):
            probe=await f.start_probe();await deferred(f)
            self.assertFalse(f.p.gate.ready.is_set());await f.feed(F['VAD'].QUIET);await probe
            self.assertTrue(f.p.gate.ready.is_set());self.assertEqual(f.p.controller.listener_ack_count,1)
            self.assertEqual((len(f.calls),f.clock.current,len(f.adopted)),(1,1,0))
        self.case(check)
    def test_non_ACK_deferred_result_is_adopted_once_without_second_ASR(self):
        async def check(f):
            probe=await f.start_probe();await deferred(f);await f.feed(F['VAD'].QUIET);await probe
            self.assertEqual((len(f.calls),f.clock.current,len(f.adopted)),(1,2,1))
            self.assertEqual(f.adopted[0][2]['request_id'],'asr-fixture-exact')
        self.case(check,text='Please explain the next point.')
    def test_confirmed_speech_cancels_old_deferred_result(self):
        async def check(f):
            probe=await f.start_probe();await deferred(f);await f.feed(F['VAD'].SPEAKING)
            await asyncio.gather(probe,return_exceptions=True)
            self.assertTrue(probe.cancelled());self.assertEqual(f.adopted,[]);self.assertEqual(f.clock.current,2);self.assertEqual(len(f.calls),1)
        self.case(check)
    def test_pending_review_replacement_fails_closed(self):
        async def check(f):
            f.provider.pending={'operation_id':'op1','expected_digest':'digest1'}
            probe=await f.start_probe();await deferred(f)
            f.provider.pending={'operation_id':'op2','expected_digest':'digest2'};await f.feed(F['VAD'].QUIET)
            with self.assertRaisesRegex(RuntimeError,'review_changed'):await probe
            self.assertEqual(f.adopted,[]);self.assertFalse(f.p.gate.ready.is_set());self.assertEqual(f.p.controller.listener_ack_count,0)
        self.case(check)
    def test_same_pending_object_changed_digest_fails_closed(self):
        async def check(f):
            f.provider.pending={'operation_id':'op1','expected_digest':'digest1'}
            probe=await f.start_probe();await deferred(f)
            f.provider.pending['expected_digest']='changed';await f.feed(F['VAD'].QUIET)
            with self.assertRaisesRegex(RuntimeError,'review_changed'):await probe
            self.assertEqual(f.adopted,[]);self.assertEqual(len(f.calls),1)
        self.case(check)
    def test_pending_review_changed_during_ASR_fails_before_adoption(self):
        async def check(f):
            f.provider.pending={'operation_id':'op1','expected_digest':'digest1'}
            probe=await f.start_probe()
            f.provider.pending={'operation_id':'op2','expected_digest':'digest2'};f.release.set()
            with self.assertRaisesRegex(RuntimeError,'review_changed'):await probe
            self.assertEqual(f.adopted,[]);self.assertEqual(f.p.controller.listener_ack_count,0);self.assertEqual(len(f.calls),1)
        self.case(check)
    def test_same_operation_digest_with_mutable_delivery_fields_is_not_rejected(self):
        async def check(f):
            f.provider.pending={'operation_id':'op1','expected_digest':'digest1','prompt_delivered':False}
            probe=await f.start_probe()
            f.provider.pending={'operation_id':'op1','expected_digest':'digest1','prompt_delivered':True,'delivery_generation':1}
            await deferred(f);f.provider.pending['delivery_generation']=2
            await f.feed(F['VAD'].QUIET);await probe
            self.assertEqual(len(f.adopted),1);self.assertEqual(f.p.controller.listener_ack_count,0);self.assertEqual(len(f.calls),1)
        self.case(check)
    def test_shutdown_settles_waiter_without_adopting_or_resuming_old_owner(self):
        async def check(f):
            probe=await f.start_probe();await deferred(f);await f.p.shutdown();await probe
            self.assertTrue(f.p.closing);self.assertTrue(f.p.overlap_onset_settled.is_set())
            self.assertEqual(f.adopted,[]);self.assertEqual(f.p.controller.listener_ack_count,0);self.assertEqual(len(f.calls),1)
        self.case(check)
    def test_unsettled_result_has_bounded_failure_not_ASR_retry(self):
        async def check(f):
            probe=await f.start_probe();await deferred(f)
            with self.assertRaisesRegex(RuntimeError,'onset_settlement_timeout'):await probe
            self.assertEqual(len(f.calls),1);self.assertEqual(f.adopted,[]);self.assertFalse(f.p.gate.ready.is_set())
        self.case(check,deadline=.02)
    def test_recoverable_ASR_error_during_STARTING_fails_closed_not_silent_discard(self):
        async def check(f):
            probe=await f.start_probe();await f.feed(F['VAD'].STARTING);f.release.set()
            with self.assertRaisesRegex(RuntimeError,'onset'):await probe
            self.assertEqual(len(f.calls),1);self.assertEqual(f.adopted,[])
        self.case(check,asr_error=True)



"""One real-constructor/normal-attachment regression; fake VAD/ASR, no devices.

ROOT: ME3_PROJECT_ROOT. OVERLAY: ME3_F2_OVERLAY or ME3_LATENCY_STAGE.
The parent may merge this class into its staged test module unchanged.
"""
import ast
import asyncio
import importlib
import importlib.util
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch
ROOT = Path(os.environ.get('ME3_PROJECT_ROOT', '/opt/ME3Deployment'))
OVERLAY = Path(os.environ.get('ME3_F2_OVERLAY', os.environ.get('ME3_LATENCY_STAGE', str(ROOT))))

def _load_actual(name, path):
    parent, _, leaf = name.rpartition('.')
    if parent:
        importlib.import_module(parent)
    existing = sys.modules.get(name)
    if existing is not None:
        if Path(existing.__file__).resolve() != path.resolve():
            raise AssertionError('exact_staged_module_required_without_rebinding')
        return existing
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    if parent:
        setattr(sys.modules[parent], leaf, module)
    return module

def _run_no_io(coroutine):
    tree = ast.parse((ROOT / 'tests/test_speech_phrasing243.py').read_text(encoding='utf-8'))
    names = {'_NoIOSelector', '_NoIOLoop', 'run_audio_fixture'}
    nodes = [n for n in tree.body if isinstance(n, (ast.ClassDef, ast.FunctionDef)) and n.name in names]
    scope = dict(asyncio=asyncio, time=time, sys=sys)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'existing_no_io_runner', 'exec'), scope)
    scope['run_audio_fixture'](coroutine)

class NormalAttachedPipelineSettlement(unittest.TestCase):

    def test_actual_pipeline_normal_overlap_retains_completed_asr_across_starting(self):
        sys.path.insert(0, str(ROOT))
        pipeline = _load_actual('me3_completion.pipeline', OVERLAY / 'me3_completion/pipeline.py')
        agent_overlap = _load_actual('me3_agent.overlap', OVERLAY / 'me3_agent/overlap.py')
        normal = importlib.import_module('me3_assistant.overlap')
        fixture = importlib.import_module('tests.test_completion_pipeline')
        self.assertIs(fixture.build_pipeline, pipeline.build_pipeline)
        self.assertIs(normal.attach_overlap, agent_overlap.attach)
        from onit_candidate.conversation import controller_class
        from pipecat.frames.frames import InputAudioRawFrame
        from pipecat.processors.frame_processor import FrameDirection
        from pipecat.audio.vad.vad_analyzer import VADState

        async def scenario():
            events = fixture.Events()
            entered, release = (asyncio.Event(), asyncio.Event())

            class Clients(fixture.Clients):

                async def call(self, role, generation, **arguments):
                    if role != 'asr':
                        raise AssertionError('unexpected_tts_or_other_provider_call')
                    entered.set()
                    return await super().call(role, generation, **arguments)

            class Provider:
                pending = None
                model_calls = 0

                async def answer(self, history):
                    self.model_calls += 1
                    raise AssertionError('listener_ack_must_not_dispatch_reasoning')
            clients = Clients()
            clients.answer = 'Okay.'
            clients.block = release
            provider = Provider()
            transport = fixture.Transport()
            with patch('duplex_audio.make_duplex_transport', return_value=(transport, transport.state)), patch('pipecat.audio.vad.silero.SileroVADAnalyzer', fixture.VAD), patch.object(pipeline, 'LocalEndpoint', fixture.Endpoint), patch.object(pipeline, 'ConversationController', controller_class()):
                _, _, _, processor = pipeline.build_pipeline(fixture.Settings(api_key=''), events, clients, provider)
            normal.attach(processor, clients, provider, events)
            frames = []

            async def push(frame, *arguments):
                frames.append(frame)
            processor.push_frame = push

            async def feed(state, count=1):
                processor.vad.state = state
                for _ in range(count):
                    await processor.process_frame(InputAudioRawFrame(audio=b'\x01\x00' * 320, sample_rate=16000, num_channels=1), FrameDirection.DOWNSTREAM)
                await asyncio.sleep(0)

            async def until(predicate):
                async with asyncio.timeout(2):
                    while not predicate():
                        await asyncio.sleep(0.001)

            async def old_response():
                await asyncio.Event().wait()
            try:
                self.assertTrue(processor.overlap_onset_settled.is_set())
                processor.controller.spawn(old_response())
                old = processor.controller.task
                await asyncio.sleep(0)
                processor.output_task = old
                processor.output_generation = processor.controller.clock.current
                processor.output_kind = 'response'
                generation = processor.controller.clock.current
                await feed(VADState.STARTING, 5)
                await feed(VADState.SPEAKING, 12)
                self.assertFalse(processor.gate.ready.is_set())
                await feed(VADState.STOPPING, 35)
                await feed(VADState.QUIET)
                await until(entered.is_set)
                probe = processor.probe
                await feed(VADState.STARTING)
                self.assertFalse(processor.overlap_onset_settled.is_set())
                release.set()
                await until(lambda: any((r['event'] == 'completion_overlap_onset_deferred' for r in events.rows)))
                self.assertFalse(probe.done())
                self.assertFalse(processor.gate.ready.is_set())
                self.assertFalse(old.done())
                await feed(VADState.QUIET)
                await probe
                await feed(VADState.QUIET)
                self.assertTrue(processor.overlap_onset_settled.is_set())
                self.assertTrue(processor.gate.ready.is_set())
                self.assertFalse(old.done())
                self.assertEqual(processor.controller.clock.current, generation)
                self.assertEqual(processor.controller.listener_ack_count, 1)
                self.assertEqual(processor.controller.turns, 0)
                self.assertEqual(len(clients.calls), 1)
                self.assertEqual(clients.calls[0][:2], ('asr', generation + 1))
                self.assertEqual(provider.model_calls, 0)
                self.assertEqual(sum((r['event'] == 'completion_user_backchannel' for r in events.rows)), 1)
                self.assertTrue(any((r['event'] == 'agent_overlap_deadline' and r['recognition_deadline_seconds'] == 35 for r in events.rows)))
                self.assertFalse(processor.pipeline_failed.is_set())
            finally:
                release.set()
                await processor.cleanup()
        _run_no_io(scenario())
