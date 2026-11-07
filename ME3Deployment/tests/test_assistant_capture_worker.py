"""No audio devices or provider calls: bounded AEC ownership and delivery."""
import asyncio,platform,sys,threading,time,unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from me3_assistant.acoustic_echo import EchoControl,attach

class Engine:
    def __init__(self,entered=None,release=None):self.entered=entered;self.release=release;self.closed=0;self.thread=None
    def process(self,mic,ref):
        self.thread=threading.get_ident()
        if self.entered:self.entered.set();self.release.wait(2)
        return mic
    def close(self):self.closed+=1

class AttachmentTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self,engine):
        delivered=[];marked=[];posts=[];loop=asyncio.get_running_loop()
        inp=NS(_audio_in_callback=lambda *a:None,record_capture=lambda status,now:marked.append((status,now)),deliver_capture=lambda *a:delivered.append(a),capture_ack=lambda:(None,99))
        out=NS(callback=lambda *a:(bytes(3840),99));transport=NS(input=lambda:inp,output=lambda:out,close=AsyncMock(),state=NS(loop=loop,post=lambda *a,**kw:posts.append((a,kw))))
        processor=NS(pipeline_failed=asyncio.Event());echo=EchoControl(engine);events=NS(emit=lambda *a,**kw:None)
        attach((None,None,transport,processor),NS(output_rate=48000,output_channels=2),echo,events,threaded_capture=True)
        return inp,transport,processor,echo,delivered,marked,posts
    async def test_callback_ack_does_not_wait_for_slow_native_processing(self):
        entered=threading.Event();release=threading.Event();e=Engine(entered,release)
        inp,t,p,echo,delivered,marked,posts=self.fixture(e)
        try:
            self.assertEqual(inp._audio_in_callback(bytes([7])*640,320,{'current_time':10,'input_buffer_adc_time':10},0),(None,99))
            self.assertTrue(await asyncio.to_thread(entered.wait,1));self.assertEqual(delivered,[])
            self.assertEqual(len(marked),1);self.assertNotEqual(e.thread,threading.get_ident())
            closer=asyncio.create_task(t.close());await asyncio.sleep(.01);self.assertFalse(closer.done())
            release.set();await asyncio.wait_for(closer,1);self.assertEqual(e.closed,1);self.assertTrue(echo.capture_worker.closed.is_set())
        finally:release.set();await t.close()
    async def test_device_input_overflow_still_stops_before_any_delivery(self):
        inp,t,p,echo,delivered,marked,posts=self.fixture(Engine())
        try:
            inp._audio_in_callback(bytes(640),320,{'current_time':10,'input_buffer_adc_time':10},2);await asyncio.sleep(.01)
            self.assertTrue(p.pipeline_failed.is_set());self.assertEqual(delivered,[])
            self.assertEqual(posts[0][1]['code'],'speaker_aec_capture_discontinuity')
        finally:await t.close()

class QueueTests(unittest.TestCase):
    def worker(self,*args,**kw):
        from me3_assistant.capture_worker import CaptureWorker
        return CaptureWorker(*args,**kw)
    def test_FIFO_original_capture_time_and_PCM_survive_processing_delay(self):
        entered=threading.Event();release=threading.Event();finished=threading.Event();seen=[];outputs=[];faults=[];clean=[]
        def process(pcm,info,stamp):
            if not seen:entered.set();release.wait(2)
            seen.append((pcm[0],stamp,info['n']));return pcm
        def deliver(pcm,*a):outputs.append(pcm[0]);finished.set() if len(outputs)==3 else None
        w=self.worker(process,deliver,lambda *a:faults.append(a),lambda:clean.append(True))
        try:
            self.assertTrue(w.submit(bytes([1])*640,320,{'n':1},0,100.));self.assertTrue(entered.wait(1))
            info={'n':2};w.submit(bytes([2])*640,320,info,0,100.02);info['n']=999;w.submit(bytes([3])*640,320,{'n':3},0,100.04)
            release.set();self.assertTrue(finished.wait(1));self.assertEqual(seen,[(1,100.,1),(2,100.02,2),(3,100.04,3)]);self.assertEqual(outputs,[1,2,3]);self.assertEqual(faults,[])
        finally:release.set();self.assertTrue(w.close());self.assertEqual(clean,[True])
    def test_queue_overrun_stops_without_raw_audio_fallback_or_unbounded_retention(self):
        entered=threading.Event();release=threading.Event();faults=[];delivered=[]
        def process(*a):entered.set();release.wait(2);return a[0]
        w=self.worker(process,lambda *a:delivered.append(a),lambda e,stage:faults.append(str(e)),lambda:None)
        try:
            w.submit(bytes(640),320,{},0,100);self.assertTrue(entered.wait(1))
            for i in range(5):self.assertTrue(w.submit(bytes(640),320,{},0,100))
            self.assertFalse(w.submit(bytes(640),320,{},0,100));self.assertEqual(faults,['speaker_aec_capture_queue_overrun']);self.assertLessEqual(w.queue.qsize(),5)
        finally:release.set();self.assertTrue(w.close())
        self.assertEqual(delivered,[]);self.assertEqual(w.queue.qsize(),0);self.assertFalse(w.submit(bytes(640),320,{},0,100))
    def test_native_or_cleanup_failure_is_not_reported_as_success(self):
        failed=threading.Event();faults=[]
        def process(*a):raise ValueError('fixture native failure')
        w=self.worker(process,lambda *a:self.fail('raw audio delivered'),lambda *a:(faults.append(a),failed.set()),lambda:None)
        w.submit(bytes(640),320,{},0,1);self.assertTrue(failed.wait(1));self.assertTrue(w.close());self.assertEqual(len(faults),1)
        def cleanup():raise RuntimeError('fixture cleanup failure')
        w=self.worker(lambda *a:a[0],lambda *a:None,lambda *a:None,cleanup);self.assertFalse(w.close());self.assertTrue(w.cleanup_failed)

@unittest.skipUnless(sys.platform=='linux' and platform.machine()=='aarch64','Actual Pi WebRTC artifact required')
class NativeQueueTests(unittest.TestCase):
    def test_real_native_worker_matches_serial_engine_for_synthetic_50Hz_stream(self):
        from me3_assistant.capture_worker import CaptureWorker
        from me3_assistant.webrtc_echo import WebRtcEcho
        import array,math
        serial=WebRtcEcho();threaded=WebRtcEcho();expected=[];actual=[];faults=[];done=threading.Event()
        def deliver(pcm,*a):actual.append(pcm);done.set() if len(actual)==100 else None
        w=CaptureWorker(lambda pcm,info,stamp:threaded.process(pcm,bytes(640)),deliver,lambda *a:faults.append(a),threaded.close)
        try:
            start=time.monotonic()
            for i in range(100):
                pcm=array.array('h',[round(500*math.sin((i*320+j)*.1)) for j in range(320)]).tobytes();expected.append(serial.process(pcm,bytes(640)))
                self.assertTrue(w.submit(pcm,320,{},0,start+i*.02));time.sleep(max(0,start+(i+1)*.02-time.monotonic()))
            self.assertTrue(done.wait(2));self.assertEqual(faults,[]);self.assertEqual(actual,expected)
        finally:serial.close();self.assertTrue(w.close())
