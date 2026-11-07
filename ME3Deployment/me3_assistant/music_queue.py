"""Bounded playlists and session queues; called only by the private control lifecycle."""
import asyncio,json,os,uuid
from pathlib import Path
from me3_assistant.library import plain_file

MAX_TRACKS=20
MAX_PLAYLISTS=20

def name(value):
    if not isinstance(value,str) or not 1<=len(value)<=80 or value!=value.strip() or value in {'.','..'} or any(c in value for c in '/\\') or not all(c.isprintable() for c in value):raise ValueError('invalid_playlist_name')
    return value

def ids(value):
    from me3_assistant.music import track_id
    if not isinstance(value,list) or not 1<=len(value)<=MAX_TRACKS or len(json.dumps(value))>2500:raise ValueError('invalid_queue_tracks')
    return [track_id(x) for x in value]

class Playlists:
    """Only this fixed metadata file is writable; reading never creates it."""
    def __init__(self,catalog):self.catalog=catalog;self.root=catalog.root;self.path=self.root/'.me3-playlists.json'
    def load(self):
        if self.root.is_symlink() or self.root.resolve()!=self.root:raise ValueError('playlist_root_changed')
        if self.path.is_symlink():raise ValueError('playlist_store_untrusted')
        if not self.path.exists():return {'owner':'me3-playlists-v1','revision':0,'playlists':{}}
        st=self.path.lstat()
        if self.path.is_symlink() or not plain_file(st) or st.st_size>65536:raise ValueError('playlist_store_untrusted')
        with self.path.open('rb') as f:
            after=os.fstat(f.fileno())
            if (st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns) or not plain_file(after):raise ValueError('playlist_store_changed')
            data=json.loads(f.read(65537))
        if set(data)!={'owner','revision','playlists'} or data['owner']!='me3-playlists-v1' or type(data['revision']) is not int or data['revision']<0 or not isinstance(data['playlists'],dict) or len(data['playlists'])>MAX_PLAYLISTS:raise ValueError('playlist_store_untrusted')
        for key,value in data['playlists'].items():name(key);ids(value)
        return data
    def list(self):
        data=self.load();return {'revision':data['revision'],'playlists':[{'name':k,'track_count':len(v)} for k,v in sorted(data['playlists'].items())],'read_only':True}
    def read(self,key):
        data=self.load();key=name(key)
        if key not in data['playlists']:raise ValueError('playlist_not_found')
        return {'name':key,'track_ids':list(data['playlists'][key]),'revision':data['revision'],'read_only':True}
    def save(self,key,tracks,revision):
        from me3_assistant.guard import lock
        key=name(key);tracks=ids(tracks);lockpath=self.path.with_suffix('.lock')
        if lockpath.is_symlink() or lockpath.exists() and not plain_file(lockpath.lstat()):raise ValueError('playlist_lock_untrusted')
        with lock(lockpath):
            data=self.load()
            if data['revision']!=revision:raise ValueError('playlist_revision_changed')
            if key not in data['playlists'] and len(data['playlists'])>=MAX_PLAYLISTS:raise ValueError('playlist_count_limit')
            data['playlists'][key]=tracks;data['revision']+=1;raw=(json.dumps(data,ensure_ascii=False,separators=(',',':'))+'\n').encode('utf-8')
            if len(raw)>65536:raise ValueError('playlist_store_limit')
            temporary=self.root/('.me3-playlists-'+uuid.uuid4().hex+'.tmp')
            try:
                fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
                with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
                if self.path.is_symlink() or self.path.exists() and not plain_file(self.path.lstat()):raise ValueError('playlist_store_untrusted')
                os.replace(temporary,self.path)
            finally:
                if temporary.exists():temporary.unlink()
            return data['revision']

class Queue:
    def __init__(self,catalog,player):
        self.catalog=catalog;self.player=player;self.playlists=Playlists(catalog);self.upcoming=[];self.revision=0;self.autoplay=False;self.epoch=None;self.program=None;self.error=None;self.failed_track=None
    def snapshot(self):
        return {'revision':self.revision,'track_ids':[x['track_id'] for x in self.upcoming],'autoplay':self.autoplay,'program_id':self.program,'error':self.error,'failed_track_id':self.failed_track,'read_only':True}
    def pin(self,tracks):
        result=[]
        for key in ids(tracks):
            f,version=self.catalog.open(key);f.close();result.append({'command':'play','track_id':key,'volume':None,'file_version':version})
        return result
    def prepare(self,clean):
        command=clean['command'];extra={'queue_revision':self.revision}
        if command in {'enqueue','replace_queue','save_playlist'}:
            extra['pinned_tracks']=self.pin(clean['track_ids'])
            if command=='enqueue':ids([x['track_id'] for x in self.upcoming]+clean['track_ids'])
        elif command=='play_playlist':
            data=self.playlists.read(clean['playlist_name']);extra.update(pinned_tracks=self.pin(data['track_ids']),playlist_revision=data['revision'])
        if command=='save_playlist':extra['playlist_revision']=self.playlists.load()['revision']
        if command=='skip' and not self.upcoming:raise ValueError('queue_is_empty')
        return extra
    def invalidate(self):
        self.upcoming=[];self.autoplay=False;self.epoch=None;self.program=None;self.error=None;self.failed_track=None;self.revision+=1
    async def start_next(self,volume):
        if not self.upcoming:raise ValueError('queue_is_empty')
        track=self.upcoming.pop(0);self.revision+=1
        try:
            await self.player.play({**track,'volume':volume});self.epoch=self.player.bus.snapshot()['epoch'];self.autoplay=True
        except BaseException:
            self.error='queue_track_failed';self.failed_track=track['track_id'];self.autoplay=False;raise
    async def apply(self,proposal):
        command=proposal['command']
        if proposal['queue_revision']!=self.revision:raise ValueError('queue_revision_changed')
        if command=='save_playlist':
            # Recheck every pinned identity immediately before persistent write.
            for track in proposal['pinned_tracks']:
                f,version=self.catalog.open(track['track_id']);f.close()
                if version!=track['file_version']:raise ValueError('music_file_changed')
            revision=self.playlists.save(proposal['playlist_name'],proposal['track_ids'],proposal['playlist_revision']);return {'playlist_name':proposal['playlist_name'],'playlist_revision':revision,'track_ids':proposal['track_ids']}
        if command=='play_playlist' and self.playlists.load()['revision']!=proposal['playlist_revision']:raise ValueError('playlist_revision_changed')
        if command in {'enqueue','replace_queue','play_playlist'}:
            self.error=None;self.failed_track=None
            if command in {'replace_queue','play_playlist'}:self.upcoming=[]
            self.upcoming.extend(proposal['pinned_tracks']);self.revision+=1
            if self.program is None:self.program=proposal['operation_id']
            if command=='play_playlist':self.program=proposal['operation_id'];await self.start_next(proposal['volume'])
            else:
                status=self.player.status();self.autoplay=status['state'] in {'playing','paused'};self.epoch=status.get('epoch')
        elif command=='skip':
            status=self.player.status();self.program=self.program or proposal['operation_id'];await self.start_next(status['volume_percent'])
        elif command=='clear_queue':self.invalidate()
        else:raise ValueError('unsupported_queue_control')
        from me3_assistant.music import fingerprint
        return {'queue_revision':self.revision,'program_id':self.program,'upcoming_digest':fingerprint([x['track_id'] for x in self.upcoming])}
    async def tick(self):
        if not self.autoplay:return
        state=self.player.status()
        if state.get('epoch')!=self.epoch:
            self.autoplay=False;self.error='playback_epoch_changed';return
        if state['state']=='error':self.autoplay=False;self.error='decoder_or_track_failure';return
        if state['state']!='ended':return
        if not self.upcoming:self.autoplay=False;return
        try:await self.start_next(state['volume_percent'])
        except asyncio.CancelledError:raise
        except Exception:pass # Explicit failed state remains; never advance/retry again.
