"""Provider/device-free calibrated stream-clock and real transport-hook guards."""
import asyncio,array,selectors,time,unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,patch
import me3_assistant.acoustic_echo as A
pcm=lambda value,n:array.array('h',[value]*n).tobytes()

class NoIOSelector(selectors.SelectSelector):
    def select(self,timeout=None):
        if self.get_map():raise RuntimeError('fixture_IO_denied')
        if timeout:time.sleep(min(timeout,.01))
        return []

class NoIOLoop(asyncio.SelectorEventLoop):
    def __init__(self):super().__init__(selector=NoIOSelector())
    def _make_self_pipe(self):self._ssock=None;self._csock=None
    def _close_self_pipe(self):pass

class Engine:
    def __init__(self):self.calls=[]
    def process(self,mic,ref):self.calls.append((mic,ref));return mic
    def close(self):pass

class Tests(unittest.TestCase):
    def fixture(self,periods=3,*,output_current=100,input_current=900,host=1):
        e=A.EchoControl(Engine());e.output_callback_periods=periods
        ticks=iter((host-.001,host+.001,host-.001,host+.001))
        with patch.object(A,'time',NS(monotonic=lambda:next(ticks))):
            e.playback_clock.calibrate(NS(get_time=lambda:output_current))
            e.capture_clock.calibrate(NS(get_time=lambda:input_current))
        return e
    def test_actual_callback_77_then_38ms_arrival_does_not_reverse_60ms_DAC(self):
        e=self.fixture();posts=[];failed=[];clock=[1.0]
        block=pcm(1100,5760)
        async def close():pass
        output=NS(callback=lambda *args:(block,99));inp=NS(_audio_in_callback=lambda *args:(None,99))
        loop=NS(is_closed=lambda:False,call_soon_threadsafe=lambda fn,*args:fn(*args))
        transport=NS(input=lambda:inp,output=lambda:output,close=close,state=NS(loop=loop,post=lambda *args,**kw:posts.append((args,kw))))
        A.attach((None,None,transport,NS(pipeline_failed=NS(set=lambda:failed.append(True)))),NS(output_rate=48000,output_channels=2),e,NS(emit=lambda *args,**kw:None))
        with patch.object(A,'time',NS(monotonic=lambda:clock[0])):
            for i,arrival in enumerate((1.0,1.07766,1.115824)):
                clock[0]=arrival
                info=dict(current_time=100+i*.06,output_buffer_dac_time=100.08+i*.06)
                self.assertEqual(output.callback(None,2880,info,0),(block,99))
        self.assertFalse(posts);self.assertFalse(failed);self.assertFalse(e.faulted)
        stamps=[stamp for stamp,_ in e.reference.chunks]
        self.assertEqual(len(stamps),9)
        for i,stamp in enumerate(stamps):self.assertAlmostEqual(stamp,1.08+i*.02)
        self.assertTrue(all(list(values)==[1100]*320 for _,values in e.reference.chunks));e.close()
    def test_true_raw_DAC_reversal_is_fatal_without_reference_mutation(self):
        e=self.fixture();b=pcm(600,5760)
        e.playback(b,dict(current_time=100,output_buffer_dac_time=100.08),now=1)
        before=tuple(e.reference.chunks)
        with self.assertRaisesRegex(ValueError,'playback_clock_reversed') as exc:e.playback(b,dict(current_time=100.06,output_buffer_dac_time=100.07),now=1.06)
        self.assertAlmostEqual(exc.exception.reference_delta_seconds,-.01);self.assertEqual(tuple(e.reference.chunks),before);e.close()
    def test_true_raw_current_reversal_is_fatal_even_with_advancing_DAC(self):
        e=self.fixture();b=pcm(600,5760);e.playback(b,dict(current_time=100,output_buffer_dac_time=100.08),now=1)
        with self.assertRaisesRegex(ValueError,'timestamp_invalid'):e.playback(b,dict(current_time=99.99,output_buffer_dac_time=100.14),now=1.06)
        e.close()
    def test_separate_origins_keep_initial_ADC_DAC_offset_and_near_end(self):
        e=self.fixture(1,output_current=2000,input_current=1000,host=50);mic=pcm(700,320);far=pcm(2000,1920)
        for i in range(3):
            e.playback(far,dict(current_time=2000+i*.02,output_buffer_dac_time=2000.02+i*.02),now=50+(0,.037,.041)[i])
            self.assertEqual(e.capture(mic,dict(current_time=1000+i*.02,input_buffer_adc_time=1000.02+i*.02),now=50+(0,.035,.042)[i]),mic)
        self.assertEqual(len(e.engine.calls),3)
        self.assertTrue(all(any(ref) and near==mic for near,ref in e.engine.calls))
        self.assertIsNot(e.capture_clock.mapper,e.playback_clock.mapper);self.assertEqual(e.capture_clock.calibration['uncertainty_seconds'],e.playback_clock.calibration['uncertainty_seconds']);e.close()
    def test_batched_ADC_uses_one_anchor_and_exact_four_real_frames(self):
        e=self.fixture();far=pcm(1300,5760);e.playback(far,dict(current_time=100,output_buffer_dac_time=100),now=1)
        e.playback(far,dict(current_time=100.06,output_buffer_dac_time=100.06),now=1.077)
        mic=pcm(500,320)
        for i in range(4):self.assertEqual(e.capture(mic,dict(current_time=900,input_buffer_adc_time=900+i*.02),now=1),mic)
        self.assertEqual(len(e.engine.calls),4);self.assertTrue(all(any(ref) for _,ref in e.engine.calls));e.close()
    def test_raw_ADC_reversal_or_duplicate_is_fatal(self):
        for sample in (900,899.99):
            e=self.fixture();e.capture(pcm(700,320),dict(current_time=900,input_buffer_adc_time=900),now=1)
            with self.assertRaisesRegex(ValueError,'capture_discontinuity'):e.capture(pcm(700,320),dict(current_time=900.02,input_buffer_adc_time=sample),now=1.02)
            self.assertEqual(len(e.engine.calls),1);e.close()
    def test_active_underflow_and_mixed_silent_prefix_remain_fatal(self):
        for block in (pcm(1000,5760),bytes(3840)+pcm(1000,1920)+bytes(3840)):
            e=self.fixture();e.playback(bytes(11520),dict(current_time=100,output_buffer_dac_time=100),now=1);before=tuple(e.reference.chunks)
            with self.assertRaisesRegex(ValueError,'playback_discontinuity'):e.playback(block,dict(current_time=100.06,output_buffer_dac_time=100.06),now=1.06,status=4)
            self.assertEqual(tuple(e.reference.chunks),before);e.close()
    def test_valid_idle_underflow_rate_bound_is_unchanged(self):
        e=self.fixture()
        for i in range(20):e.playback(bytes(11520),dict(current_time=100+i*.06,output_buffer_dac_time=100+i*.06),now=1+i*.06)
        for i in (20,21):self.assertTrue(e.playback(bytes(11520),dict(current_time=100+i*.06,output_buffer_dac_time=100+i*.06),now=1+i*.06,status=4))
        with self.assertRaisesRegex(ValueError,'idle_underflow_limit'):e.playback(bytes(11520),dict(current_time=101.32,output_buffer_dac_time=101.32),now=2.32,status=4)
        e.close()
    def test_invalid_nonfinite_offset_and_repeated_raw_clock_are_not_excused(self):
        for info in (dict(current_time=float('nan'),output_buffer_dac_time=100),dict(current_time=100,output_buffer_dac_time=100.51)):
            e=self.fixture()
            with self.assertRaises(ValueError):e.playback(bytes(11520),info,now=1)
            self.assertFalse(e.reference.chunks);e.close()
        e=self.fixture();info=dict(current_time=100,output_buffer_dac_time=100);e.playback(bytes(11520),info,now=1)
        with self.assertRaisesRegex(ValueError,'playback_clock_reversed'):e.playback(bytes(11520),info,now=1.06)
        e.close()
    def test_cancellation_still_discards_old_PCM_without_changing_clock(self):
        from duplex_audio import PlayoutBuffer
        b=PlayoutBuffer(23040);e=self.fixture();first=pcm(1000,5760)
        self.assertEqual(b.offer(first,0),'accepted');e.playback(b.take(11520)[0],dict(current_time=100,output_buffer_dac_time=100),now=1)
        self.assertEqual(b.clear(),(1,0));self.assertEqual(b.offer(first,0),'stale')
        e.playback(b.take(11520)[0],dict(current_time=100.06,output_buffer_dac_time=100.06),now=1.077)
        self.assertFalse(any(e.reference.chunks[-1][1]));self.assertAlmostEqual(e.playback_clock.calibration['bracket_seconds'],.002);e.close()
    def test_unbound_and_second_calibration_fail_without_get_time_retry(self):
        e=A.EchoControl(Engine());e.output_callback_periods=3
        with self.assertRaises(ValueError):e.playback(bytes(11520),dict(current_time=100,output_buffer_dac_time=100),now=1)
        calls=[];stream=NS(get_time=lambda:calls.append(True) or 100);ticks=iter((.999,1.001))
        with patch.object(A,'time',NS(monotonic=lambda:next(ticks))):e.playback_clock.calibrate(stream)
        with self.assertRaises(ValueError):e.playback_clock.calibrate(stream)
        self.assertEqual(calls,[True]);self.assertFalse(e.reference.chunks);e.close()
    def test_actual_transport_hooks_calibrate_before_start_without_callback_PA_calls(self):
        import pyaudio
        from duplex_audio import CaptureAudio,make_duplex_transport
        from pipecat.transports.base_output import BaseOutputTransport
        seen=[];calls=[];inside_callback=[False]
        class Stream:
            def __init__(self,raw):self.raw=raw;self.started=False;self.closed=False
            def get_time(self):
                if inside_callback[0]:raise AssertionError('PortAudio_called_inside_callback')
                calls.append(('time',self.raw,self.started));return self.raw
            def get_output_latency(self):return .06
            def get_input_latency(self):return .08
            def start_stream(self):self.started=True
            def close(self):self.closed=True
        class Audio:
            def open(self,**kw):
                seen.append(kw);self.last=Stream(900 if kw.get('input') else 100);return self.last
        async def case():
            with patch.object(pyaudio,'PyAudio',return_value=Audio()),patch.object(BaseOutputTransport,'setup',AsyncMock()),patch.object(BaseOutputTransport,'sample_rate',property(lambda self:48000)):
                transport,state=make_duplex_transport(NS(output_rate=48000,output_channels=2,input_device=0,output_device=1),NS(emit=lambda *a,**k:None))
                state.configure_output_callback(3);state.configure_input_callback(4);e=A.EchoControl(Engine());e.output_callback_periods=3
                A.attach((None,None,transport,NS(pipeline_failed=NS(set=lambda:None))),NS(output_rate=48000,output_channels=2),e,NS(emit=lambda *a,**kw:None))
                ticks=iter((.999,1.001,.999,1.001))
                with patch.object(A,'time',NS(monotonic=lambda:next(ticks))):
                    await transport.output().setup(None)
                    capture=CaptureAudio(transport.audio,state).open(input=True,rate=16000,channels=1,frames_per_buffer=320)
                self.assertEqual(calls,[('time',100,False),('time',900,False)])
                self.assertEqual((seen[0]['start'],seen[0]['frames_per_buffer']),(False,2880));self.assertEqual((seen[1]['start'],seen[1]['frames_per_buffer']),(False,1280))
                transport.state.buffer.offer(pcm(800,5760),0);inside_callback[0]=True
                with patch.object(A,'time',NS(monotonic=lambda:1.06)):output=transport.output().callback(None,2880,dict(current_time=100.06,output_buffer_dac_time=100.06),0)
                inside_callback[0]=False;self.assertEqual(output[0],pcm(800,5760));self.assertEqual(len(calls),2)
                self.assertFalse(capture.started);capture.close();transport.output().stream.close();e.close()
        with asyncio.Runner(loop_factory=NoIOLoop) as runner:runner.run(case())
    def test_input_hook_none_preserves_arguments_and_failed_binding_closes_once(self):
        from duplex_audio import CaptureAudio
        seen=[];closed=[]
        stream=NS(close=lambda:closed.append(True),get_input_latency=lambda:.08)
        audio=NS(open=lambda **kw:seen.append(kw) or stream)
        state=NS(input_callback_periods=1,input_clock_calibrator=None,events=NS(emit=lambda *a,**kw:None))
        args=dict(input=True,rate=16000,channels=1,frames_per_buffer=320)
        self.assertIs(CaptureAudio(audio,state).open(**args),stream);self.assertEqual(seen[0],args)
        def fail(s):raise ValueError('bad_calibration')
        state.input_clock_calibrator=fail
        with self.assertRaisesRegex(ValueError,'bad_calibration'):CaptureAudio(audio,state).open(**args)
        self.assertIs(seen[1]['start'],False);self.assertEqual(closed,[True])

if __name__=='__main__':unittest.main()
