"""Fixed reminder PCM into the existing mixer only; never opens audio or ASR."""
import array,math,re,sys,threading
from collections import deque

RATE=48000
def chime():
    values=array.array('h')
    for frame in range(RATE//2):
        t=frame/RATE;local=t if t<.2 else t-.28
        value=0
        if 0<=local<.2:
            envelope=min(1,local/.005,(.2-local)/.005)
            value=round(4200*max(0,envelope)*math.sin(2*math.pi*(880 if t<.2 else 1320)*local))
        values.extend((value,value))
    if sys.byteorder!='little':values.byteswap()
    return values.tobytes()

class ReminderAudioLane:
    def __init__(self,blocked,live):
        self.blocked=blocked;self.live=live;self.closed=False;self.lock=threading.Lock()
        self.queue=deque();self.reports=deque();self.seen=set();self.pcm=chime()
    def available(self):
        with self.lock:return not self.closed and not self.blocked() and self.live() and len(self.queue)<4
    def enqueue(self,reminder_id,claim_id):
        if any(not isinstance(v,str) or not re.fullmatch('[a-f0-9]{32}',v) for v in (reminder_id,claim_id)):raise ValueError('exact_reminder_audio_claim_required')
        with self.lock:
            if self.closed or self.blocked() or not self.live() or len(self.queue)>=4:return False
            if claim_id in self.seen:raise ValueError('reminder_audio_claim_not_replayed')
            self.seen.add(claim_id)
            self.queue.append(dict(reminder_id=reminder_id,claim_id=claim_id,offset=0));return True
    def _finish(self,item,state):
        self.reports.append(dict(reminder_id=item['reminder_id'],claim_id=item['claim_id'],state=state,mixed_bytes=item['offset'],audible_verified=False))
    def cancel(self,reminder_id):
        with self.lock:
            kept=deque()
            for item in self.queue:
                if item['reminder_id']==reminder_id:self._finish(item,'INTERRUPTED')
                else:kept.append(item)
            self.queue=kept
    def mix(self,ordinary):
        if not isinstance(ordinary,bytes) or len(ordinary)%4:raise ValueError('reminder_mix_requires_48k_stereo_PCM')
        with self.lock:
            if self.closed or self.blocked() or not self.live():
                while self.queue:self._finish(self.queue.popleft(),'INTERRUPTED')
                return ordinary
            if not self.queue:return ordinary
            item=self.queue[0];size=min(len(ordinary),len(self.pcm)-item['offset'])
            tone=array.array('h');tone.frombytes(self.pcm[item['offset']:item['offset']+size])
            output=array.array('h');output.frombytes(ordinary)
            if sys.byteorder!='little':tone.byteswap();output.byteswap()
            for i,value in enumerate(tone):output[i]=max(-32768,min(32767,output[i]+value))
            item['offset']+=size
            if item['offset']==len(self.pcm):self._finish(self.queue.popleft(),'PCM_MIXED')
            if sys.byteorder!='little':output.byteswap()
            return output.tobytes()
    def drain(self):
        with self.lock:result=list(self.reports);self.reports.clear();return result
    def close(self):
        with self.lock:
            self.closed=True
            while self.queue:self._finish(self.queue.popleft(),'INTERRUPTED')

def attach(result,settings,*,blocked,echo):
    if settings.output_rate!=RATE or settings.output_channels!=2 or echo is None:raise ValueError('reminder_chime_requires_existing_speaker_AEC_48k_stereo')
    transport=result[2];output=transport.output();original=output.callback
    lane=ReminderAudioLane(blocked,lambda:not getattr(transport,'_closed',True) and getattr(output,'stream',None) is not None and not getattr(echo,'closed',True) and not getattr(echo,'faulted',True))
    def mixed(*args):
        pcm,flag=original(*args)
        return lane.mix(pcm),flag
    output.callback=mixed
    return lane
