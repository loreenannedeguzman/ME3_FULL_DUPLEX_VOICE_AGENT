"""Bounded local music decoding. FFmpeg reads only an already opened local file."""
import asyncio,hashlib,json,math,os,re,shutil,stat,sys,time
from pathlib import Path
from me3_assistant.library import plain_file
from me3_assistant.music_audio import MusicBus

FORMATS={'.wav':'wav','.mp3':'mp3','.flac':'flac','.ogg':'ogg'}
MAX_FILE=64*1024*1024

def decoder_input(stream,suffix):
    """Select bounded known demuxers from the already verified open file.

    A local.mp3 can actually contain MP4/AAC. MOV may need to seek backwards
    after its end-of-file index; Linux's fixed inherited stdin descriptor keeps
    that capability without reopening a user-supplied pathname. External data
    references remain disabled. No arbitrary probing, URL or playlist demuxer.
    """
    stream.seek(0)
    try:fd=stream.fileno()
    except (AttributeError,OSError):fd=None
    if fd is not None and hasattr(os,'pread'):
        head=os.pread(fd,12,0)
    else:
        head=stream.read(12);stream.seek(0)
        # BufferedReader.seek(0) may rewind only its user-space buffer, leaving
        # the inherited descriptor at4096. FFmpeg reads that descriptor directly.
        if fd is not None:os.lseek(fd,0,os.SEEK_SET)
    fmt=FORMATS[suffix]
    if len(head)>=12 and head[4:8]==b'ftyp':
        if not sys.platform.startswith('linux'):
            raise ValueError('seekable_media_requires_linux_descriptor')
        return ['-protocol_whitelist','file','-f','mov','-enable_drefs','0','-use_absolute_path','0','-i','/proc/self/fd/0']
    return ['-protocol_whitelist','pipe','-f',fmt,'-i','pipe:0']
def fingerprint(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def track_id(name):
    if not isinstance(name,str) or not 1<=len(name)<=120 or name!=name.strip() or name.startswith('.') or '..' in name or not re.fullmatch(r'[\w .()\-]+',name) or Path(name).suffix.lower() not in FORMATS or name.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*[f'COM{i}' for i in range(1,10)],*[f'LPT{i}' for i in range(1,10)]}:raise ValueError('invalid_track_id')
    return name

class Catalog:
    def __init__(self,root):
        root=Path(root)
        if root.is_symlink():raise ValueError('music_root_link')
        self.root=root.resolve()
    def inventory(self):
        if self.root.is_symlink() or self.root.resolve()!=self.root:raise ValueError('music_root_changed')
        if not self.root.exists():return {'available':False,'tracks':[],'read_only':True}
        tracks=[]
        for p in self.root.iterdir():
            try:track_id(p.name)
            except ValueError:continue
            info=p.lstat()
            if p.is_symlink() or not plain_file(info):continue
            if info.st_size>MAX_FILE:continue
            tracks.append({'track_id':p.name,'byte_count':info.st_size})
            if len(tracks)>100:raise ValueError('music_catalog_limit')
        return {'available':True,'tracks':sorted(tracks,key=lambda x:x['track_id'].casefold()),'read_only':True}
    def list(self,limit=20,offset=0):
        from me3_assistant.music_catalog import page,describe
        inventory=self.inventory()
        return page([describe(self,row) for row in inventory['tracks']],limit,offset,len(inventory['tracks']),inventory['available'])
    def search(self,query,field='any',limit=20,offset=0):
        from me3_assistant.music_catalog import search
        return search(self,query,field,limit,offset)
    def open(self,name):
        name=track_id(name);p=self.root/name;before=p.lstat()
        if self.root.is_symlink() or p.is_symlink() or p.resolve().parent!=self.root or not plain_file(before) or before.st_size>MAX_FILE:raise ValueError('music_file_not_permitted')
        fd=os.open(p,os.O_RDONLY|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0));f=os.fdopen(fd,'rb');after=os.fstat(f.fileno())
        if not plain_file(after) or (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns):f.close();raise ValueError('music_file_changed')
        return f,{'track_id':name,'size':after.st_size,'mtime_ns':after.st_mtime_ns,'device':after.st_dev,'inode':after.st_ino}

def intent(command,track=None,volume=None,track_ids=None,playlist_name=None):
    if command in {'enqueue','replace_queue','clear_queue','skip','save_playlist','play_playlist'}:
        from me3_assistant.music_queue import ids,name
        if track is not None:raise ValueError('unexpected_music_track')
        clean={'command':command,'track_id':None,'volume':None}
        if command in {'enqueue','replace_queue','save_playlist'}:clean['track_ids']=ids(track_ids)
        elif track_ids is not None:raise ValueError('unexpected_queue_tracks')
        if command in {'save_playlist','play_playlist'}:clean['playlist_name']=name(playlist_name)
        elif playlist_name is not None:raise ValueError('unexpected_playlist_name')
        if command=='play_playlist':
            volume=33 if volume is None else volume
            if type(volume) is float and math.isfinite(volume) and volume.is_integer():volume=int(volume)
            if type(volume) is not int or not 0<=volume<=100:raise ValueError('music_volume_limit')
            clean['volume']=volume
        elif volume is not None:raise ValueError('unexpected_music_volume')
        return clean
    if track_ids is not None or playlist_name is not None:raise ValueError('unexpected_queue_arguments')
    if command not in {'play','pause','resume','stop','volume'}:raise ValueError('unsupported_music_control')
    if command=='play':track=track_id(track);volume=33 if volume is None else volume
    elif track is not None:raise ValueError('unexpected_music_track')
    if command in {'play','volume'}:
        # JSON Schema integer includes an exactly integral JSON number20.0.
        # Canonicalize that representation without rounding or changing value.
        if type(volume) is float and math.isfinite(volume) and volume.is_integer() and 0<=volume<=100:volume=int(volume)
        if type(volume) is not int or not 0<=volume<=100:raise ValueError('music_volume_limit')
    elif volume is not None:raise ValueError('unexpected_music_volume')
    return {'command':command,'track_id':track,'volume':volume}

async def reap_decoder(proc):
    # A paused asyncio stdout transport can keep wait() pending after exit.
    # Drain the pipe while reaping; never wait unboundedly on a full decoder pipe.
    if proc.returncode is None:
        try:proc.terminate()
        except ProcessLookupError:pass
    try:await asyncio.wait_for(proc.communicate(),2)
    except TimeoutError:
        if proc.returncode is None:
            try:proc.kill()
            except ProcessLookupError:pass
        await asyncio.wait_for(proc.communicate(),2)

class Player:
    def __init__(self,catalog,bus):self.catalog=catalog;self.bus=bus;self.decoder=None;self.task=None;self.track=None;self.error=None
    def status(self):
        return {**(self.bus.snapshot() if self.bus else {'state':'unavailable','output_attached':False}),'track_id':self.track,'decoder_available':bool(shutil.which('ffmpeg')),'error':self.error,'read_only':True}
    async def stop(self):
        if self.bus:self.bus.control(state=0)
        task=self.task;self.task=None
        if task:task.cancel();await asyncio.gather(task,return_exceptions=True)
        self.decoder=None
    async def play(self,proposal):
        if not self.bus or not self.bus.snapshot()['output_attached']:raise ValueError('assistant_output_not_attached')
        decoder=shutil.which('ffmpeg')
        if not decoder:raise ValueError('ffmpeg_not_installed')
        f,version=self.catalog.open(proposal['track_id'])
        if version!=proposal['file_version']:f.close();raise ValueError('music_file_changed')
        await self.stop();self.track=proposal['track_id'];self.error=None;epoch=self.bus.start(proposal['volume'])
        proc=None
        try:
            proc=await asyncio.create_subprocess_exec(decoder,'-hide_banner','-loglevel','error','-nostdin','-threads','1','-filter_threads','1',*decoder_input(f,Path(self.track).suffix.lower()),'-map','0:a:0','-vn','-sn','-dn','-map_metadata','-1','-ac','2','-ar','48000','-t','600','-f','s16le','pipe:1',stdin=f,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
            self.decoder=proc
            async def feed():
                tail=b''
                try:
                    async with asyncio.timeout(600):
                        while True:
                            data=await proc.stdout.read(3840)
                            if not data:break
                            now=os.fstat(f.fileno())
                            if (now.st_size,now.st_mtime_ns)!=(version['size'],version['mtime_ns']):raise ValueError('music_file_changed')
                            data=tail+data;tail=data[len(data)//4*4:];data=data[:len(data)//4*4]
                            while data:
                                result=self.bus.offer(data,epoch)
                                if result=='stale':return
                                if result=='accepted':break
                                await asyncio.sleep(.01)
                        code=await proc.wait()
                        if code or tail:raise ValueError('decoder_failed')
                        while self.bus.snapshot()['buffered_bytes'] and self.bus.snapshot()['epoch']==epoch:await asyncio.sleep(.02)
                        self.bus.control(state=3,epoch=epoch)
                except asyncio.CancelledError:raise
                except Exception:
                    self.error='decoder_or_track_failure';self.bus.control(state=4,epoch=epoch)
                finally:
                    try:await reap_decoder(proc)
                    finally:f.close()
            self.task=asyncio.create_task(feed())
            async with asyncio.timeout(3):
                while not self.bus.snapshot()['consumed_frames']:
                    if self.task.done():raise ValueError('playback_did_not_start')
                    await asyncio.sleep(.02)
        except BaseException:
            await self.stop()
            if proc is not None:await reap_decoder(proc)
            if not f.closed:f.close()
            raise
    async def apply(self,p):
        if p['command']=='play':await self.play(p);return
        if not self.bus:raise ValueError('assistant_output_not_attached')
        state=self.bus.snapshot()['state']
        if p['command']=='stop':await self.stop()
        elif p['command']=='pause':
            if state!='playing':raise ValueError('music_not_playing')
            self.bus.control(state=2)
        elif p['command']=='resume':
            if state!='paused':raise ValueError('music_not_paused')
            self.bus.control(state=1)
        else:
            if state not in {'playing','paused'}:raise ValueError('music_not_active')
            self.bus.control(volume=p['volume'])
