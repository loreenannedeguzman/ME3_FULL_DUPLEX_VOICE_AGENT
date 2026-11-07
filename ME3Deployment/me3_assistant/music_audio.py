"""Optional bounded music input to the existing output callback; never captures voice."""
import array,contextlib,mmap,os,struct,sys,time,threading
from collections import deque
from pathlib import Path

HEADER=struct.Struct('<8sQIIQQQQ')
MAGIC=b'ME3MUS1!'
CAPACITY=76800
STATES={0:'stopped',1:'playing',2:'paused',3:'ended',4:'error'}

class MusicBus:
    def __init__(self,path,create=False):
        self.path=Path(path);self.closed=False;self.thread_lock=threading.Lock()
        flags=os.O_RDWR|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0)
        if create:flags|=os.O_CREAT|os.O_EXCL
        self.fd=os.open(self.path,flags,0o600)
        try:
            if create:os.ftruncate(self.fd,HEADER.size+CAPACITY)
            if os.fstat(self.fd).st_size!=HEADER.size+CAPACITY:raise ValueError('invalid_music_bus')
            self.mem=mmap.mmap(self.fd,HEADER.size+CAPACITY)
            if create:self.mem[:HEADER.size]=HEADER.pack(MAGIC,0,0,33,0,0,0,0)
            self._header()
        except BaseException:os.close(self.fd);raise
    @contextlib.contextmanager
    def lock(self,blocking=True):
        acquired=False;local=self.thread_lock.acquire(blocking)
        if not local:
            yield False;return
        try:
            if os.name=='nt':
                import msvcrt
                os.lseek(self.fd,0,os.SEEK_SET);msvcrt.locking(self.fd,msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.fd,fcntl.LOCK_EX|(0 if blocking else fcntl.LOCK_NB))
            acquired=True
        except OSError:
            if blocking:self.thread_lock.release();raise
        try:yield acquired
        finally:
            if acquired:
                if os.name=='nt':
                    import msvcrt
                    os.lseek(self.fd,0,os.SEEK_SET);msvcrt.locking(self.fd,msvcrt.LK_UNLCK,1)
                else:
                    import fcntl
                    fcntl.flock(self.fd,fcntl.LOCK_UN)
            self.thread_lock.release()
    def _header(self):
        h=list(HEADER.unpack(self.mem[:HEADER.size]))
        if h[0]!=MAGIC or h[2] not in STATES or h[3]>100 or not 0<=h[5]<=h[4] or h[4]-h[5]>CAPACITY:raise ValueError('invalid_music_bus')
        return h
    def _save(self,h):self.mem[:HEADER.size]=HEADER.pack(*h)
    def snapshot(self):
        with self.lock():h=self._header()
        return {'epoch':h[1],'state':STATES[h[2]],'volume_percent':h[3],'buffered_bytes':h[4]-h[5],'consumed_frames':h[6]//4,'output_attached':0<time.monotonic_ns()-h[7]<2_000_000_000,'measurement':'music frames consumed by output callback; may be muted; not physical audibility'}
    def start(self,volume):
        with self.lock():
            h=self._header();h[1]+=1;h[2]=1;h[3]=volume;h[4]=h[5]=h[6]=0;self._save(h);return h[1]
    def control(self,state=None,volume=None,epoch=None):
        with self.lock():
            h=self._header()
            if epoch is not None and epoch!=h[1]:return False
            if volume is not None:
                if type(volume) is not int or not 0<=volume<=100:raise ValueError('music_volume_limit')
                h[3]=volume
            if state is not None:
                if state not in STATES:raise ValueError('music_state_invalid')
                h[2]=state
                if state==0:h[1]+=1;h[4]=h[5]=h[6]=0;self.mem[HEADER.size:]=bytes(CAPACITY)
            self._save(h);return True
    def offer(self,data,epoch):
        if len(data)%4 or len(data)>CAPACITY:raise ValueError('music_PCM_alignment')
        with self.lock():
            h=self._header()
            if h[1]!=epoch or h[2] not in {1,2}:return 'stale'
            if h[4]-h[5]+len(data)>CAPACITY:return 'full'
            start=h[4]%CAPACITY;first=min(len(data),CAPACITY-start)
            self.mem[HEADER.size+start:HEADER.size+start+first]=data[:first]
            if first<len(data):self.mem[HEADER.size:HEADER.size+len(data)-first]=data[first:]
            h[4]+=len(data);self._save(h);return 'accepted'
    def mix(self,voice,user_speaking=False):
        # Never block or change voice when music is inactive/locked. Ring contains music only.
        if self.closed:return voice
        with self.lock(False) as acquired:
            if not acquired:return voice
            h=self._header();h[7]=time.monotonic_ns()
            if h[2]!=1 or h[4]==h[5]:self._save(h);return voice
            count=min(len(voice),h[4]-h[5]);count-=count%4;start=h[5]%CAPACITY;first=min(count,CAPACITY-start)
            music=self.mem[HEADER.size+start:HEADER.size+start+first]
            if first<count:music+=self.mem[HEADER.size:HEADER.size+count-first]
            h[5]+=count
            # User percentage spans0..100; full scale retains the former0.60
            # maximum gain and existing voice ducking. No system volume change.
            gain=0 if user_speaking else h[3]/100*.60*(.15 if any(voice) else 1)
            h[6]+=count
            self._save(h)
        if not gain:return voice
        original=array.array('h',voice);background=array.array('h',music)
        if sys.byteorder!='little':original.byteswap();background.byteswap()
        for i,value in enumerate(background):original[i]=max(-32768,min(32767,original[i]+round(value*gain)))
        if sys.byteorder!='little':original.byteswap()
        return original.tobytes()
    def close(self,destroy=False):
        if self.closed:return
        if destroy:self.control(state=0)
        self.mem.close();os.close(self.fd);self.closed=True
        if destroy:self.path.unlink()

def attach_music(result,settings,bus,events=None):
    if settings.output_rate!=48000 or settings.output_channels!=2:raise ValueError('music_requires_existing_48k_stereo_output')
    _,_,transport,processor=result;output=transport.output();callback=output.callback;analyze=processor.vad.analyze_audio;user=False;disabled=False
    if hasattr(processor,'preroll'):
        processor.preroll=deque(processor.preroll,maxlen=50) #1s at20ms: keep quiet initial consonants before VAD confirms.
    def music_active():
        try:return bus.snapshot()['state']=='playing'
        except Exception:return True #Mixer owns the actual bus-fault stop.
    processor.task_ack_music_active=music_active # host-only current mixer observation
    for name in ('backchannel','semantic_cue'):
        original=getattr(processor,name,None)
        if original is None:continue
        async def cue(*args,_original=original,**kwargs):
            if music_active():
                transport.state.post('assistant_music_listener_cue_suppressed',reason='music_active',microphone_gated=False);return
            return await _original(*args,**kwargs)
        setattr(processor,name,cue)
    if hasattr(processor,'controller') and hasattr(processor,'semantic_cue'):
        processor.controller.listener_cue=processor.semantic_cue
    original_play=getattr(processor,'play',None)
    if original_play is not None:
        async def play(pcm,generation,kind='response',**kwargs):
            # Recheck immediately before output if music began during synthesis.
            if kind in {'backchannel','planning_ack','task_ack'} and music_active():
                transport.state.post('assistant_music_listener_cue_suppressed',reason='music_started_before_cue_playout',microphone_gated=False);return
            return await original_play(pcm,generation,kind,**kwargs)
        processor.play=play
    report=events.emit if events is not None else transport.state.post
    report('assistant_music_capture_policy',preroll_ms=processor.preroll.maxlen*20 if hasattr(processor,'preroll') else None,listener_cues_during_music=False,microphone_gated=False)
    async def vad(pcm):
        nonlocal user
        state=await analyze(pcm)
        if state.name=='SPEAKING' and not getattr(vad,'confirmed',False):
            report('assistant_music_capture_onset',music_state=bus.snapshot()['state'],retained_prefix_frames=len(processor.preroll) if hasattr(processor,'preroll') else None,retained_prefix_limit_ms=processor.preroll.maxlen*20 if hasattr(processor,'preroll') else None,capture_gated=False,audio_retained=False)
        vad.confirmed=state.name=='SPEAKING';user=state.name!='QUIET';return state
    def mixed(*args):
        nonlocal disabled
        voice,flag=callback(*args)
        if disabled:return voice,flag
        try:return bus.mix(voice,user),flag
        except Exception:
            disabled=True;transport.state.post('music_mix_disabled',reason='music_bus_fault',voice_unchanged=True)
            return voice,flag
    processor.vad.analyze_audio=vad;output.callback=mixed
