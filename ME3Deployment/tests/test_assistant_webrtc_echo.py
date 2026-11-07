"""Native boundary, causal capture, fail-closed startup and real Pi signal checks."""
import array,ctypes,hashlib,json,math,os,platform,sys,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from task_mcp.client import ROOT
from me3_assistant.webrtc_echo import WebRtcEcho,verified_library
from me3_assistant.acoustic_echo import speaker_echo,attach,EchoControl

class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.base=self.root/'data/native/webrtc-aec';self.base.mkdir(parents=True)
        self.lib=self.base/'libme3_apm.so';self.lib.write_bytes(b'fixed library fixture');self.lib.chmod(0o700)
        self.cpp=self.root/'me3_assistant/native/webrtc_shim.cpp';self.cpp.parent.mkdir(parents=True);self.cpp.write_bytes(b'fixed source fixture')
        self.receipt=self.base/'build.json';self.data={'abi':1,'profile':'AEC3_lowNS_noAGC','source_cpp_sha256':hashlib.sha256(self.cpp.read_bytes()).hexdigest(),'library_sha256':hashlib.sha256(self.lib.read_bytes()).hexdigest()};self.save()
    def save(self):self.receipt.write_text(json.dumps(self.data));self.receipt.chmod(0o600)
    def test_valid_build_and_library_tamper_detected(self):
        self.assertEqual(verified_library(self.root),self.lib.resolve())
        self.lib.write_bytes(b'tampered library')
        with self.assertRaisesRegex(ValueError,'hash_mismatch'):verified_library(self.root)
    def test_source_or_profile_or_abi_changes_require_new_build(self):
        for field,value in (('abi',2),('profile','no_echo'),('source_cpp_sha256','0'*64)):
            with self.subTest(field=field):
                old=self.data[field];self.data[field]=value;self.save()
                with self.assertRaisesRegex(ValueError,'hash_mismatch'):verified_library(self.root)
                self.data[field]=old;self.save()
    def test_missing_artifact_and_untrusted_permissions_are_rejected(self):
        self.lib.unlink()
        with self.assertRaisesRegex(ValueError,'build_required'):verified_library(self.root)
        self.lib.write_bytes(b'fixed library fixture')
        if os.name!='nt':
            self.lib.chmod(0o666)
            with self.assertRaisesRegex(ValueError,'permissions_invalid'):verified_library(self.root)
    @unittest.skipIf(os.name=='nt','Windows symlink creation requires privilege')
    def test_library_symlink_is_not_loaded(self):
        self.lib.unlink();self.lib.symlink_to(self.cpp)
        with self.assertRaisesRegex(ValueError,'path_invalid'):verified_library(self.root)

class BoundaryTests(unittest.TestCase):
    def engine(self,lib=None):
        lib=lib or Mock();lib.me3_apm_abi.return_value=1;lib.me3_apm_profile.return_value=0x101;lib.me3_apm_new.return_value=123
        def process(ptr,mic,ref,out,count):
            for i in range(count):out[i]=mic[i]
            return 0
        lib.me3_apm_process.side_effect=process
        with patch('me3_assistant.webrtc_echo.sys.platform','linux'),patch('me3_assistant.webrtc_echo.platform.machine',return_value='aarch64'),patch('me3_assistant.webrtc_echo.verified_library',return_value=Path('/fixed/test.so')),patch('me3_assistant.webrtc_echo.ctypes.CDLL',return_value=lib):e=WebRtcEcho()
        self.addCleanup(e.close);return e,lib
    def test_causal_buffer_keeps_near_speech_and_uses_current_reference(self):
        e,lib=self.engine();mic=array.array('h',[900]*320).tobytes();ref=array.array('h',[1200]*320).tobytes()
        self.assertEqual(e.process(mic,ref),bytes(640));self.assertEqual(e.process(bytes(640),bytes(640)),mic)
        args=lib.me3_apm_process.call_args_list[0].args;self.assertEqual(bytes(args[2]),ref);self.assertEqual(args[4],320)
    def test_invalid_frame_never_consumes_or_replaces_pending_capture(self):
        e,lib=self.engine();mic=array.array('h',[711]*320).tobytes();e.process(mic,bytes(640));calls=lib.me3_apm_process.call_count
        for m,r in ((bytes(638),bytes(640)),(bytes(640),bytes(642))):
            with self.assertRaisesRegex(ValueError,'frame_invalid'):e.process(m,r)
        self.assertEqual(lib.me3_apm_process.call_count,calls);self.assertEqual(e.process(bytes(640),bytes(640)),mic)
    def test_native_failure_is_not_silently_replaced_with_raw_audio(self):
        e,lib=self.engine();lib.me3_apm_process.side_effect=None;lib.me3_apm_process.return_value=-1
        with self.assertRaisesRegex(ValueError,'processing_failed'):e.process(bytes(640),bytes(640))
        self.assertEqual(e.previous,bytes(640))
    def test_close_serializes_with_processing_and_is_idempotent(self):
        e,lib=self.engine();entered=threading.Event();release=threading.Event()
        def blocked(*args):entered.set();self.assertTrue(release.wait(2));return 0
        lib.me3_apm_process.side_effect=blocked
        worker=threading.Thread(target=e.process,args=(bytes(640),bytes(640)));worker.start();self.assertTrue(entered.wait(2))
        closer=threading.Thread(target=e.close);closer.start();self.assertEqual(lib.me3_apm_free.call_count,0)
        release.set();worker.join(2);closer.join(2);self.assertFalse(worker.is_alive());self.assertFalse(closer.is_alive());e.close()
        lib.me3_apm_free.assert_called_once_with(123)
        with self.assertRaisesRegex(ValueError,'closed'):e.process(bytes(640),bytes(640))
    def test_invalid_native_abi_or_failed_allocation_stops_before_processing(self):
        for abi,allocated,reason in ((2,123,'abi_mismatch'),(1,None,'initialization_failed')):
            lib=Mock();lib.me3_apm_abi.return_value=abi;lib.me3_apm_profile.return_value=0x101;lib.me3_apm_new.return_value=allocated
            with patch('me3_assistant.webrtc_echo.sys.platform','linux'),patch('me3_assistant.webrtc_echo.platform.machine',return_value='aarch64'),patch('me3_assistant.webrtc_echo.verified_library',return_value=Path('/fixed/test.so')),patch('me3_assistant.webrtc_echo.ctypes.CDLL',return_value=lib):
                with self.assertRaisesRegex(ValueError,reason):WebRtcEcho()
            lib.me3_apm_process.assert_not_called()
    def test_speaker_factory_never_falls_back_when_native_artifact_invalid(self):
        with patch('me3_assistant.webrtc_echo.WebRtcEcho',side_effect=ValueError('bad build')):
            with self.assertRaisesRegex(ValueError,'bad build'):speaker_echo()
        e,lib=self.engine()
        with patch('me3_assistant.webrtc_echo.WebRtcEcho',return_value=e):
            echo=speaker_echo();self.assertIs(echo.engine,e);echo.close()

@unittest.skipUnless(sys.platform=='linux' and platform.machine()=='aarch64' and (ROOT/'data/native/webrtc-aec/build.json').is_file(),'Built Pi WebRTC artifact required')
class NativeTests(unittest.TestCase):
    def test_actual_abi_frame_and_cleanup(self):
        e=WebRtcEcho()
        try:
            for _ in range(8):self.assertEqual(len(e.process(bytes(640),bytes(640))),640)
            with self.assertRaises(ValueError):e.process(bytes(638),bytes(640))
        finally:e.close();e.close()
        with self.assertRaises(ValueError):e.process(bytes(640),bytes(640))
    def test_positive_echo_and_independent_doubletalk_preserved(self):self.metrics(640)
    def test_negative_subframe_echo_and_independent_doubletalk_preserved(self):self.metrics(-128)
    def metrics(self,lag):
        import numpy as np
        rng=np.random.default_rng(531);count=12*16000;far=np.zeros(count);far[:8*16000]=rng.uniform(-6000,6000,8*16000)
        near=np.zeros(count);t=np.arange(2*16000)/16000;tone=2000*np.sin(2*np.pi*260*t);near[5*16000:7*16000]=tone
        # A separate modulated multi-frequency signal checks preservation after output ends.
        t2=np.arange(16000)/16000;voice=2200*(.6+.4*np.sin(2*np.pi*3*t2))*(np.sin(2*np.pi*223*t2)+.3*np.sin(2*np.pi*447*t2));near[10*16000:11*16000]=voice
        mic=near+rng.uniform(-10,10,count)
        if lag>=0:mic[lag:]+=.25*far[:-lag]
        else:mic[:lag]+=.25*far[-lag:]
        e=WebRtcEcho();blocks=[];worst=0
        try:
            for off in range(0,count,320):
                at=time.monotonic();blocks.append(np.frombuffer(e.process(np.rint(mic[off:off+320]).astype('<i2').tobytes(),np.rint(far[off:off+320]).astype('<i2').tobytes()),dtype='<i2').astype(float));worst=max(worst,time.monotonic()-at)
        finally:e.close()
        out=np.concatenate(blocks);lags=range(320,961);corr=[]
        for d in lags:
            part=out[160000+d:176000+d];corr.append(float(np.dot(part,voice)/np.sqrt(max(1,np.dot(part,part)*np.dot(voice,voice)))))
        d=320+int(np.argmax(corr));part=out[160000+d:176000+d];gain=float(np.dot(part,voice)/np.dot(voice,voice))
        residue=out[3*16000+d:4*16000+d];original=mic[3*16000:4*16000];reduction=10*np.log10(np.dot(original,original)/max(1,np.dot(residue,residue)))
        part=out[5*16000+d:7*16000+d];double_gain=2*abs(np.dot(part,np.exp(-2j*np.pi*260*t)))/len(t)/2000
        self.assertGreaterEqual(reduction,20);self.assertGreaterEqual(max(corr),.8);self.assertGreaterEqual(gain,.6);self.assertLessEqual(gain,1.4);self.assertGreaterEqual(double_gain,.6);self.assertLessEqual(double_gain,1.4);self.assertLess(worst,.02)

if __name__=='__main__':unittest.main()
