"""Bounded transient FIFO; native AEC never runs in PortAudio's callback."""
import queue,threading,time

class CaptureWorker:
    def __init__(self,process,deliver,fault,cleanup,capacity=5,batch_frames=1):
        if type(capacity) is not int or not (1<=capacity<=5 or capacity in (8,12)):raise ValueError('invalid_capture_queue_capacity')
        if type(batch_frames) is not int or batch_frames not in (1,4) or (capacity==12)!=(batch_frames==4):raise ValueError('invalid_capture_batch_profile')
        self.batch_frames=batch_frames;self.dispatch_deadline_seconds=.32 if batch_frames==4 else None
        self.process=process;self.deliver=deliver;self.fault=fault;self.cleanup=cleanup
        self.queue=queue.Queue(capacity);self.stop=threading.Event();self.closed=threading.Event()
        self.state_lock=threading.Lock();self.cleanup_failed=False
        self.frames=0;self.peak=0;self.discarded_on_close=0;self.max_wait_seconds=0
        self.thread=threading.Thread(target=self.run,name='me3-capture-aec',daemon=True);self.thread.start()
    def submit(self,pcm,count,info,status,captured_at):
        return self.submit_batch(((pcm,count,info,status,captured_at),))
    def submit_batch(self,frames):
        # The only producer is the real callback; admit its whole block or
        # fail the session before queuing a partial callback. Consumer dequeue
        # can only increase available space while state_lock owns admission.
        with self.state_lock:
            if self.stop.is_set():return False
            if type(frames) not in (tuple,list) or len(frames) not in (1,self.batch_frames):
                self.stop.set();self.fault(ValueError('speaker_aec_requires_20ms_16k_mono'),'capture');return False
            packed=[];queued_at=time.monotonic()
            for pcm,count,info,status,captured_at in frames:
                if not isinstance(pcm,bytes) or len(pcm)!=640 or count!=320:
                    self.stop.set();self.fault(ValueError('speaker_aec_requires_20ms_16k_mono'),'capture');return False
                packed.append((pcm,count,dict(info),status,captured_at,queued_at))
            if self.queue.qsize()+len(packed)>self.queue.maxsize:
                self.stop.set();self.fault(ValueError('speaker_aec_capture_queue_overrun'),'capture');return False
            for item in packed:self.queue.put_nowait(item)
            self.peak=max(self.peak,self.queue.qsize());return True
    def run(self):
        try:
            while not self.stop.is_set():
                try:item=self.queue.get(timeout=.02)
                except queue.Empty:continue
                if self.stop.is_set():self.discarded_on_close+=1;break
                pcm,count,info,status,captured_at,queued_at=item
                self.max_wait_seconds=max(self.max_wait_seconds,time.monotonic()-queued_at)
                try:
                    if self.dispatch_deadline_seconds is not None and time.monotonic()-queued_at>self.dispatch_deadline_seconds:
                        self.discarded_on_close+=1;raise ValueError('speaker_aec_capture_queue_overrun')
                    cleaned=self.process(pcm,info,captured_at)
                    if self.stop.is_set():continue
                    if self.dispatch_deadline_seconds is not None and time.monotonic()-queued_at>self.dispatch_deadline_seconds:
                        self.discarded_on_close+=1;raise ValueError('speaker_aec_capture_queue_overrun')
                    self.deliver(cleaned,count,info,status);self.frames+=1
                except Exception as exc:self.stop.set();self.fault(exc,'capture')
        finally:
            with self.state_lock:
                self.stop.set()
                while True:
                    try:self.queue.get_nowait();self.discarded_on_close+=1
                    except queue.Empty:break
            try:self.cleanup()
            except Exception as exc:self.cleanup_failed=True;self.fault(exc,'capture_cleanup')
            finally:self.closed.set()
    def close(self,timeout=2):
        with self.state_lock:self.stop.set()
        self.thread.join(timeout)
        return self.closed.is_set() and not self.cleanup_failed
