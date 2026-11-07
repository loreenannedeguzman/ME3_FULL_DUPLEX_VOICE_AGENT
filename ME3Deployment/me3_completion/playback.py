"""Suspend a response without canceling it; cancellation still clears epochs."""
import asyncio,threading
from array import array
class SuspendableBuffer:
    def __init__(self,delegate):self.delegate=delegate;self.lock=threading.RLock();self.paused=False;self.smoother=None
    def enable_smoothing(self,rate,channels):
        with self.lock:
            if self.smoother is not None:raise RuntimeError("smoothing_already_configured")
            self.smoother=TransitionEnvelope(rate,channels)
    @property
    def epoch(self):return self.delegate.epoch
    @property
    def max_bytes(self):return self.delegate.max_bytes
    def suspend(self):
        with self.lock:
            if not self.paused and self.smoother:self.smoother.suspend()
            self.paused=True
    def resume(self):
        with self.lock:
            if self.paused and self.smoother:self.smoother.resume()
            self.paused=False
    def offer(self,data,epoch):
        with self.lock:return self.delegate.offer(data,epoch)
    def clear(self):
        with self.lock:
            result=self.delegate.clear()
            if self.smoother:self.smoother.reset()
            return result
    def take(self,count):
        with self.lock:
            if self.smoother:self.smoother.validate_count(count)
            if self.paused:
                data=self.smoother.silence(count) if self.smoother else bytes(count)
                return data,self.delegate.epoch,0,self.delegate._size
            data,epoch,played,queued=self.delegate.take(count)
            if self.smoother:data=self.smoother.apply(data,played)
            return data,epoch,played,queued
class PlaybackGate:
    def __init__(self,buffer):self.buffer=buffer;self.ready=asyncio.Event();self.ready.set()
    def suspend(self):self.buffer.suspend();self.ready.clear()
    def resume(self):self.buffer.resume();self.ready.set()
    async def wait(self):await self.ready.wait()


class TransitionEnvelope:
    """Five-millisecond normal-mode PCM edge envelope, before the AEC reference.

    A suspended callback drains no queued speech. Its only tail is a linear
    interpolation from the last submitted sample to zero, bounded to5ms.
    Clearing an epoch cancels that tail immediately; no old queued sample or
    old tail survives cancellation. Resume/new output fades in from zero.
    The unchanged default buffer has no envelope.
    """
    def __init__(self,rate,channels):
        if type(rate) is not int or rate not in {16000,24000,44100,48000} or type(channels) is not int or channels not in {1,2}:
            raise ValueError("unsupported_transition_format")
        self.channels=channels;self.frames=max(2,rate//200);self.reset()
    def reset(self):
        self.last=[0]*self.channels;self.tail=[0]*self.channels;self.tail_at=self.frames;self.rise_at=0
    def validate_count(self,count):
        if type(count) is not int or count<0 or count%(2*self.channels):raise ValueError("unaligned_transition_buffer")
    def suspend(self):
        self.tail=list(self.last);self.tail_at=0;self.rise_at=0
    def resume(self):
        self.tail_at=self.frames;self.rise_at=0
    def silence(self,count):
        self.validate_count(count);out=array('h',[0])*(count//2)
        for f in range(min(count//(2*self.channels),self.frames-self.tail_at)):
            self.tail_at+=1
            for ch in range(self.channels):
                out[f*self.channels+ch]=round(self.tail[ch]*(self.frames-self.tail_at)/self.frames)
        if count:self.last=list(out[-self.channels:])
        return out.tobytes()
    def apply(self,data,played):
        self.validate_count(len(data));self.validate_count(played)
        if not 0<=played<=len(data):raise ValueError("invalid_transition_played_count")
        if not played:
            # An empty queue is a new start, never resume a previous epoch.
            self.rise_at=0;self.last=[0]*self.channels
            return data
        samples=array('h');samples.frombytes(data)
        n=played//(2*self.channels)
        for f in range(min(n,self.frames-self.rise_at)):
            gain=self.rise_at/(self.frames-1);self.rise_at+=1
            for ch in range(self.channels):
                i=f*self.channels+ch;samples[i]=round(samples[i]*gain)
        self.last=list(samples[-self.channels:]) if samples else self.last
        if played<len(data):self.rise_at=0
        return samples.tobytes()
