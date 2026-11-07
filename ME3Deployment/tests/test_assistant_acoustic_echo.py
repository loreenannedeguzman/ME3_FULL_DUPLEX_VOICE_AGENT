"""Provider/device-free AEC routing, safety, and native synthetic signal checks."""
import array
import asyncio
import ctypes.util
import math
import random
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from me3_assistant.acoustic_echo import EchoControl, Reference, SpeexEcho, ResidualEcho, CausalEcho, attach, audio_mode, callback_time

def pcm(values):
    return array.array('h', values).tobytes()

def stereo(values):
    return pcm([v for x in values for v in (x, x)])

class Engine:
    def __init__(self): self.calls = []; self.closed = 0
    def process(self, mic, ref): self.calls.append((mic, ref)); return mic
    def close(self): self.closed += 1

class ReferenceTests(unittest.TestCase):
    def test_default_path_remains_earphones_and_unknown_mode_rejected(self):
        self.assertEqual(audio_mode({}), 'earphones')
        self.assertEqual(audio_mode({'audio_mode':'speaker_aec'}), 'speaker_aec')
        with self.assertRaises(ValueError): audio_mode({'audio_mode':'silent_fallback'})

    def test_separate_stream_origins_use_relative_adc_dac_offsets(self):
        self.assertAlmostEqual(callback_time({'current_time':1000,'input_buffer_adc_time':999.98}, 'input_buffer_adc_time', 50), 49.98)
        self.assertAlmostEqual(callback_time({'current_time':2000,'output_buffer_dac_time':2000.02}, 'output_buffer_dac_time', 50), 50.02)
        for value in (float('nan'),float('inf'),None,100):
            with self.assertRaises(ValueError): callback_time({'current_time':0,'input_buffer_adc_time':value},'input_buffer_adc_time',0)

    def test_actual_stereo_output_is_downmixed_and_time_aligned(self):
        r=Reference(); r.offer(pcm([1000,3000]*960), 1)
        data,covered=r.frame(1)
        self.assertEqual(covered,320); self.assertEqual(list(array.array('h',data)),[2000]*320)
        later,covered=r.frame(1.01)
        self.assertEqual(covered,160); self.assertEqual(list(array.array('h',later))[:160],[2000]*160)

    def test_no_old_reference_reused_before_after_or_in_gap(self):
        r=Reference(); r.offer(stereo([1000]*960),1); r.offer(stereo([2000]*960),1.1)
        for t in (.9,1.05,1.2): self.assertEqual(r.frame(t)[0],bytes(640))
        r.offer(bytes(3840),1.12); self.assertEqual(r.frame(1.12)[0],bytes(640))

    def test_reference_memory_is_bounded_and_reversed_clock_rejected(self):
        r=Reference()
        for i in range(100): r.offer(bytes(3840),i*.02)
        self.assertEqual(len(r.chunks),64)
        with self.assertRaises(ValueError): r.offer(bytes(3840),1)
        with self.assertRaises(ValueError): r.offer(bytes(8),3)
        r.clear(); self.assertEqual(len(r.chunks),0)

    def test_near_end_is_always_processed_while_far_end_present(self):
        e=Engine(); echo=EchoControl(e); echo.playback(stereo([2000]*960),{'current_time':0,'output_buffer_dac_time':0},now=1)
        mic=pcm([700]*320)
        self.assertEqual(echo.capture(mic,{'current_time':0,'input_buffer_adc_time':0},now=1),mic)
        self.assertTrue(any(e.calls[0][1])); self.assertEqual(echo.frames,1)
        echo.close(); echo.close(); self.assertEqual(e.closed,1)
        with self.assertRaises(ValueError): echo.capture(mic,{'current_time':0,'input_buffer_adc_time':0},now=1)

    def test_bad_frame_is_rejected_before_native_call(self):
        e=Engine(); echo=EchoControl(e)
        with self.assertRaises(ValueError): echo.capture(bytes(320),{'current_time':0,'input_buffer_adc_time':0})
        self.assertEqual(e.calls,[]); echo.close()

class CausalBufferTests(unittest.TestCase):
    def test_one_frame_delay_preserves_all_near_speech_and_current_reference(self):
        engine=Engine();echo=CausalEcho(engine);mic1=pcm([700]*320);mic2=pcm([900]*320);ref1=pcm([1200]*320);ref2=pcm([1400]*320)
        self.assertEqual(echo.process(mic1,ref1),bytes(640))
        self.assertEqual(echo.process(mic2,ref2),mic1)
        self.assertEqual(echo.process(bytes(640),bytes(640)),mic2)
        self.assertEqual(engine.calls,[(bytes(640),ref1),(mic1,ref2),(mic2,bytes(640))]);echo.close()

    def test_invalid_frame_never_replaces_pending_microphone(self):
        engine=Engine();echo=CausalEcho(engine);mic=pcm([700]*320);echo.process(mic,bytes(640))
        for bad_mic,bad_ref in ((bytes(10),bytes(640)),(bytes(640),bytes(10))):
            with self.assertRaises(ValueError):echo.process(bad_mic,bad_ref)
        self.assertEqual(echo.process(bytes(640),bytes(640)),mic);echo.close()

    def test_close_clears_pending_audio_once_and_rejects_new_capture(self):
        engine=Engine();echo=CausalEcho(engine);echo.process(pcm([700]*320),bytes(640));echo.close();echo.close()
        self.assertEqual(echo.previous,bytes(640));self.assertEqual(engine.closed,1)
        with self.assertRaises(ValueError):echo.process(bytes(640),bytes(640))

    def test_echo_control_keeps_reference_time_and_frame_size_unchanged(self):
        engine=Engine();echo=EchoControl(CausalEcho(engine));mic=pcm([800]*320)
        echo.playback(stereo([2000]*960),{'current_time':0,'output_buffer_dac_time':0},now=1)
        self.assertEqual(echo.capture(mic,{'current_time':0,'input_buffer_adc_time':0},now=1),bytes(640))
        echo.playback(stereo([2200]*960),{'current_time':0,'output_buffer_dac_time':0},now=1.02)
        self.assertEqual(echo.capture(bytes(640),{'current_time':0,'input_buffer_adc_time':0},now=1.02),mic)
        self.assertEqual(engine.calls[1],(mic,pcm([2200]*320)));echo.close()

class AttachmentTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self, final_pcm=None):
        seen=[]; output=NS(callback=lambda *args:(stereo([1300]*960) if final_pcm is None else final_pcm,99))
        inp=NS(_audio_in_callback=lambda data,*args:seen.append(data) or (None,99))
        events=NS(emit=lambda *args,**kwargs:None)
        posts=[]; state=NS(loop=asyncio.get_running_loop(),post=lambda *args,**kwargs:posts.append((args,kwargs)))
        transport=NS(input=lambda:inp,output=lambda:output,close=AsyncMock(),state=state)
        processor=NS(pipeline_failed=asyncio.Event()); echo=EchoControl(Engine())
        attach((None,None,transport,processor),NS(output_rate=48000,output_channels=2),echo,events)
        return inp,output,transport,processor,echo,seen,posts

    async def test_outer_callback_copies_final_mix_and_never_alters_output(self):
        inp,out,t,p,e,seen,posts=self.fixture()
        far,flag=out.callback(None,960,{'current_time':10,'output_buffer_dac_time':10},0)
        self.assertEqual(flag,99);self.assertEqual(far,stereo([1300]*960))
        inp._audio_in_callback(pcm([333]*320),320,{'current_time':10,'input_buffer_adc_time':10},0)
        self.assertEqual(seen[0],pcm([333]*320));self.assertFalse(p.pipeline_failed.is_set())
        self.assertEqual(len(e.engine.calls),1)
        await t.close();self.assertTrue(e.closed)

    async def test_fault_stops_session_without_unclean_asr_fallback(self):
        inp,out,t,p,e,seen,posts=self.fixture()
        inp._audio_in_callback(pcm([999]*320),320,{},0)
        await asyncio.sleep(0)
        self.assertTrue(p.pipeline_failed.is_set());self.assertEqual(seen[0],bytes(640))
        self.assertEqual(len(posts),1);self.assertEqual(e.engine.calls,[])
        await t.close()

    async def test_output_discontinuity_is_reported_and_stops(self):
        inp,out,t,p,e,seen,posts=self.fixture()
        out.callback(None,960,{'current_time':10,'output_buffer_dac_time':10},1)
        await asyncio.sleep(0);self.assertTrue(p.pipeline_failed.is_set());await t.close()

    async def test_idle_underflow_preserves_near_speech_and_reports_bounded_recovery(self):
        inp,out,t,p,e,seen,posts=self.fixture(bytes(3840))
        # Seed one second of actual final callback silence, not wall-clock idle.
        from unittest.mock import patch
        for i in range(51):
            with patch('me3_assistant.acoustic_echo.time.monotonic',return_value=i*.02):
                out.callback(None,960,{'current_time':0,'output_buffer_dac_time':0},0)
        with patch('me3_assistant.acoustic_echo.time.monotonic',return_value=1.06):
            far,flag=out.callback(None,960,{'current_time':0,'output_buffer_dac_time':0},4)
        inp._audio_in_callback(pcm([333]*320),320,{'current_time':0,'input_buffer_adc_time':0},0)
        self.assertEqual((far,flag),(bytes(3840),99));self.assertEqual(seen,[pcm([333]*320)])
        self.assertFalse(p.pipeline_failed.is_set());self.assertFalse(e.faulted)
        self.assertEqual(posts[0][0],('speaker_aec_idle_output_underflow',))
        self.assertEqual(e.reference.idle_underflows,1);await t.close()

    async def test_unknown_native_error_details_are_not_logged(self):
        inp,out,t,p,e,seen,posts=self.fixture()
        def broken(*args): raise ValueError('private untrusted native exception')
        e.engine.process=broken
        inp._audio_in_callback(pcm([999]*320),320,{'current_time':0,'input_buffer_adc_time':0},0)
        await asyncio.sleep(0)
        self.assertTrue(p.pipeline_failed.is_set())
        self.assertEqual(posts[0][1]['code'],'speaker_aec_processing_failed')
        self.assertEqual(posts[0][1]['stage'],'capture')
        self.assertNotIn('private',str(posts));await t.close()

class IdleUnderflowTests(unittest.TestCase):
    def quiet(self):
        r=Reference()
        for i in range(51):r.offer(bytes(3840),i*.02)
        return r

    def test_known_idle_underflow_can_continue_twice_but_third_stops(self):
        r=self.quiet()
        self.assertTrue(r.offer(bytes(3840),1.06,4))
        self.assertTrue(r.offer(bytes(3840),1.08,4))
        with self.assertRaisesRegex(ValueError,'idle_underflow_limit'):r.offer(bytes(3840),1.1,4)
        self.assertEqual(r.idle_underflows,2)

    def test_startup_active_recent_and_missing_history_still_fail(self):
        for setup,stamp,output in ((Reference,0,bytes(3840)),(self.quiet,1.02,stereo([1000]*960)),(self.quiet,1.3,bytes(3840))):
            with self.subTest(stamp=stamp,active=any(output)):
                with self.assertRaisesRegex(ValueError,'playback_discontinuity'):setup().offer(output,stamp,4)
        r=self.quiet();r.offer(stereo([500]*960),1.02);r.offer(bytes(3840),1.04)
        with self.assertRaisesRegex(ValueError,'playback_discontinuity'):r.offer(bytes(3840),1.08,4)

    def test_opposite_phase_stereo_is_not_digital_silence(self):
        r=self.quiet();r.offer(pcm([1000,-1000]*960),1.02);r.offer(bytes(3840),1.04)
        with self.assertRaisesRegex(ValueError,'playback_discontinuity'):r.offer(bytes(3840),1.06,4)

    def test_other_or_combined_status_and_reversed_clock_remain_fatal(self):
        for status in (1,2,5,8,12,16,32):
            with self.subTest(status=status):
                with self.assertRaisesRegex(ValueError,'playback_discontinuity'):self.quiet().offer(bytes(3840),1.02,status)
        with self.assertRaisesRegex(ValueError,'clock_reversed'):self.quiet().offer(bytes(3840),.98,4)

    def test_recent_callback_gaps_restart_silence_qualification(self):
        r=self.quiet();r.offer(bytes(3840),1.3)
        with self.assertRaisesRegex(ValueError,'playback_discontinuity'):r.offer(bytes(3840),1.32,4)

@unittest.skipUnless(ctypes.util.find_library('speexdsp'),'native SpeexDSP unavailable on this host')
class NativeTests(unittest.TestCase):
    def test_causal_margin_preserves_positive_delay_and_near_speech(self):
        r=native_metrics(CausalEcho,640)
        self.assertGreaterEqual(r['echo_reduction_db'],12)
        self.assertGreater(r['double_talk_near_gain'],.6);self.assertLess(r['double_talk_near_gain'],1.4)
        self.assertLess(r['max_processing_seconds'],.02)

    def test_negative_subframe_echo_is_causal_with_one_frame_margin(self):
        rng=random.Random(814);count=16000*8;far=[];value=0
        for _ in range(count):value=.75*value+rng.uniform(-4000,4000);far.append(round(value))
        engine=CausalEcho();original=[];clean=[]
        try:
            for offset in range(0,count,320):
                mic=[round(.4*far[n+128]+(.1*far[n-640] if n>=640 else 0)) if n+128<count else 0 for n in range(offset,offset+320)]
                out=array.array('h',engine.process(pcm(mic),pcm(far[offset:offset+320])))
                if 16000*4<=offset<16000*7:original.extend(mic);clean.extend(out)
            reduction=10*math.log10(sum(x*x for x in original)/max(1,sum(x*x for x in clean)))
            self.assertGreaterEqual(reduction,12)
        finally:engine.close()

    def test_residual_stage_preserves_double_talk_with_declared_20ms_delay(self):
        r=native_metrics(ResidualEcho,320)
        self.assertGreaterEqual(r['echo_reduction_db'],12)
        self.assertGreater(r['double_talk_near_gain'],.6)
        self.assertLess(r['double_talk_near_gain'],1.4)
        self.assertLess(r['max_processing_seconds'],.02)

    def test_delayed_echo_reduction_and_double_talk_preservation(self):
        r=native_metrics()
        self.assertGreaterEqual(r['echo_reduction_db'],12)
        self.assertGreater(r['double_talk_near_gain'],.6)
        self.assertLess(r['double_talk_near_gain'],1.4)
        self.assertLess(r['max_processing_seconds'],.02)

def native_metrics(factory=SpeexEcho,delay=0):
    from time import monotonic
    rng=random.Random(724); count=16000*10; far=[]; value=0
    for i in range(count):
        value=.75*value+rng.uniform(-4500,4500); far.append(round(value))
    native=factory(); original=[]; residual=[]; near_track=[]; double_out=[]; worst=0
    try:
        for offset in range(0,count,320):
            ref=far[offset:offset+320]; mic=[]; near=[]
            for j in range(320):
                n=offset+j; echo=round(.5*far[n-640]+.2*far[n-803]) if n>=803 else 0
                voice=round(1800*math.sin(2*math.pi*317*n/16000)) if n>=16000*7 else 0
                near.append(voice);mic.append(echo+voice)
            start=monotonic();out=list(array.array('h',native.process(pcm(mic),pcm(ref))));worst=max(worst,monotonic()-start)
            if 16000*4<=offset<16000*7: original.extend(mic);residual.extend(out)
            if offset>=16000*8: near_track.extend(near);double_out.extend(out)
        ratio=sum(x*x for x in original)/max(1,sum(x*x for x in residual))
        if delay:near_track=near_track[:-delay];double_out=double_out[delay:]
        gain=sum(x*y for x,y in zip(near_track,double_out))/sum(x*x for x in near_track)
        return {'echo_reduction_db':10*math.log10(ratio),'double_talk_near_gain':gain,'max_processing_seconds':worst,'stimulus':'seeded synthetic colored noise, 40/50ms echo, independent near-end tone; not acoustic or speech validation','provider_calls':0,'tts_calls':0,'audio_devices_opened':False}
    finally: native.close()

if __name__=='__main__':unittest.main()
