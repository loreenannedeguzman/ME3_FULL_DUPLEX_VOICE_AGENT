"""Student music MCP: reads plus private, confirmation-owned control lifecycle."""
import argparse,asyncio,logging,os,re,time,uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated,Literal
from mcp.server.fastmcp import FastMCP
from pydantic import Field,StrictInt
from task_mcp.client import ROOT
from speech_mcp.contracts import identity
from me3_assistant.music import Catalog,Player,intent,fingerprint
from me3_assistant.music_audio import MusicBus
from me3_assistant.music_queue import Queue
QUEUE_CONTROLS={'enqueue','replace_queue','clear_queue','skip','save_playlist','play_playlist'}

class MusicService:
    def __init__(self,root,bus=None):self.catalog=Catalog(root);self.bus=bus;self.player=Player(self.catalog,bus);self.pending=None;self.operations={};self.lock=asyncio.Lock();self.queue=Queue(self.catalog,self.player);self.monitor=None
    def prepare(self,command,track_id,volume,operation_id,track_ids=None,playlist_name=None):
        if not re.fullmatch('[0-9a-f]{32}',operation_id):raise ValueError('invalid_operation_id')
        if len(self.operations)>=64:raise ValueError('music_operation_limit')
        clean=intent(command,track_id,volume,track_ids,playlist_name);proposal=dict(clean)
        if command in QUEUE_CONTROLS:proposal.update(self.queue.prepare(clean))
        if command=='play':
            f,version=self.catalog.open(clean['track_id']);f.close();proposal['file_version']=version
        if command!='save_playlist' and (not self.bus or not self.bus.snapshot()['output_attached']):raise ValueError('assistant_output_not_attached')
        proposal.update(operation_id=operation_id,token=uuid.uuid4().hex,expected_digest=fingerprint(clean),epoch=self.bus.snapshot()['epoch'] if self.bus else None,expires=time.monotonic()+120)
        self.pending=proposal
        return proposal
    async def commit(self,operation_id,token,confirmation):
        if operation_id in self.operations:
            old=self.operations[operation_id]
            if token!=old['token'] or confirmation!='confirm':raise ValueError('music_confirmation_required')
            return {'operation_id':operation_id,'duplicate':True}
        p=self.pending
        if not p or operation_id!=p['operation_id'] or token!=p['token'] or confirmation!='confirm' or time.monotonic()>p['expires']:raise ValueError('music_confirmation_required')
        if p['command']!='save_playlist' and self.bus.snapshot()['epoch']!=p['epoch']:raise ValueError('music_target_changed')
        self.pending=None
        # Reserve the operation before dispatch. An uncertain effect is never replayed.
        row={'operation_id':operation_id,'token':token,'intent':{k:p[k] for k in ('command','track_id','volume','track_ids','playlist_name') if k in p},'expected_digest':p['expected_digest'],'state':'DISPATCHED'};self.operations[operation_id]=row
        try:
            if p['command'] in QUEUE_CONTROLS:row['result']=await self.queue.apply(p)
            else:
                if p['command'] in {'play','stop'}:self.queue.invalidate()
                await self.player.apply(p)
            row.update(state='COMPLETED',epoch=self.bus.snapshot()['epoch'] if self.bus else None)
            return {'operation_id':operation_id,'duplicate':False}
        except asyncio.CancelledError:
            await self.player.stop();row['state']='UNCERTAIN';raise
        except Exception:
            row['state']='UNCERTAIN';raise
    def verify(self,operation_id):
        row=self.operations.get(operation_id)
        result={'operation_id':operation_id,'row':{k:v for k,v in row.items() if k!='token'} if row else None,'snapshot':self.player.status(),'verification_source':'fresh_music_bus_state'}
        if row and row['intent']['command']=='save_playlist':result['playlist']=self.queue.playlists.read(row['intent']['playlist_name'])
        elif row and row['intent']['command'] in QUEUE_CONTROLS:result['queue']=self.queue.snapshot()
        return result
    async def start(self):
        async def monitor():
            while True:
                await asyncio.sleep(.02)
                async with self.lock:
                    try:await self.queue.tick()
                    except asyncio.CancelledError:raise
                    except Exception:self.queue.autoplay=False;self.queue.error='queue_monitor_failed'
        self.monitor=asyncio.create_task(monitor())
    async def close(self):
        if self.monitor:self.monitor.cancel();await asyncio.gather(self.monitor,return_exceptions=True)
        self.queue.invalidate()
        await self.player.stop()
        if self.bus:self.bus.close()

def server(root=None,bus_path=None):
    if root is None:
        root=ROOT/'data/music'
        if not root.resolve().is_relative_to(ROOT.resolve()):raise ValueError('music_outside_project')
    else:root=Path(root)
    service=MusicService(root,MusicBus(bus_path) if bus_path else None)
    # Like local documents, advertise actual bounded IDs at discovery. A spoken
    # title/extension is not a filesystem path; the model must choose a real ID.
    # The existing fresh open/version checks still own action preparation.
    try:track_ids=[row['track_id'] for row in service.catalog.inventory()['tracks']]
    except ValueError:track_ids=[]
    track_type=Annotated[str,Field(json_schema_extra={'enum':track_ids})] if track_ids else str
    @asynccontextmanager
    async def lifespan(app):
        await service.start()
        try:yield {}
        finally:await service.close()
    app=FastMCP('me3-local-music',lifespan=lifespan,log_level='CRITICAL')
    async def perform(g,r,operation):
        identity(g,r)
        async with service.lock:
            try:
                value=operation();value=await value if asyncio.iscoroutine(value) else value
                return {'ok':True,**value,'generation':g,'request_id':r,'server_role':'utility'}
            except asyncio.CancelledError:raise
            except Exception:return {'ok':False,'error':'music_operation_failed_or_unavailable','generation':g,'request_id':r,'server_role':'utility'}
    @app.tool()
    async def list_tracks(generation:StrictInt,request_id:str,limit:Annotated[StrictInt,Field(ge=1,le=25)]=20,offset:Annotated[StrictInt,Field(ge=0)]=0)->dict:
        """List real local music IDs and bounded title/artist/album tags. Follow next_offset for more; total_tracks is the full catalog count. Missing artist is unknown, never invented. Filename titles are explicitly labelled. Read-only; no playback or streaming."""
        return await perform(generation,request_id,lambda:service.catalog.list(limit,offset))
    @app.tool()
    async def search_tracks(query:Annotated[str,Field(min_length=1,max_length=120)],generation:StrictInt,request_id:str,field:Literal['any','title','artist','album']='any',limit:Annotated[StrictInt,Field(ge=1,le=25)]=20,offset:Annotated[StrictInt,Field(ge=0)]=0)->dict:
        """Search the actual local library by title, artist, album or filename; case/accent insensitive AND-token substring match. Uses bounded real ID3/FLAC tags where present, otherwise title from filename and unknown artist. Zero matches is not a streaming search. Return exact track_id; ask which if multiple matches. Read-only; never starts playback."""
        return await perform(generation,request_id,lambda:service.catalog.search(query,field,limit,offset))
    @app.tool()
    async def get_playback_status(generation:StrictInt,request_id:str)->dict:
        """Read this assistant session's music state, track and player-only volume. Software submission does not prove physical audibility. Session stop also stops its music."""
        return await perform(generation,request_id,service.player.status)
    @app.tool()
    async def get_queue(generation:StrictInt,request_id:str)->dict:
        """Read this session's upcoming exact track IDs, automatic continuation and explicit queue errors. Does not start or change playback."""
        return await perform(generation,request_id,service.queue.snapshot)
    @app.tool()
    async def list_playlists(generation:StrictInt,request_id:str)->dict:
        """List saved local playlist names/counts, never create files or start playback. Use read_playlist for actual track IDs."""
        return await perform(generation,request_id,service.queue.playlists.list)
    @app.tool()
    async def read_playlist(playlist_name:Annotated[str,Field(min_length=1,max_length=80)],generation:StrictInt,request_id:str)->dict:
        """Read one exact saved local playlist and track IDs. A playlist is bounded to20tracks; missing names are unavailable, never fabricated. No playback or write."""
        return await perform(generation,request_id,lambda:service.queue.playlists.read(playlist_name))
    @app.tool()
    async def prepare_music_control(command:Literal['play','pause','resume','stop','volume','enqueue','replace_queue','clear_queue','skip','save_playlist','play_playlist'],operation_id:str,generation:StrictInt,request_id:str,track_id:track_type|None=None,volume:StrictInt|None=None,track_ids:Annotated[list[track_type],Field(min_length=1,max_length=20)]|None=None,playlist_name:Annotated[str,Field(min_length=1,max_length=80)]|None=None)->dict:
        """Propose one local music control; no playback effect. For play select the exact track_id from the schema/catalog, interpreting spoken titles/extensions against those IDs; never construct a filename from spoken words. Ask if ambiguous. For other commands omit track_id. Volume is absolute0–100, default33 for play; no system volume change. Direct delivered confirmation and separate local-action allowance are required."""
        return await perform(generation,request_id,lambda:service.prepare(command,track_id,volume,operation_id,track_ids,playlist_name))
    @app.tool()
    async def commit_music_control(operation_id:str,token:str,confirmation:str,generation:StrictInt,request_id:str)->dict:
        """Private controller endpoint; never expose directly to the model."""
        return await perform(generation,request_id,lambda:service.commit(operation_id,token,confirmation))
    @app.tool()
    async def read_music_operation(operation_id:str,generation:StrictInt,request_id:str)->dict:
        """Private independent read-back of an operation and the current playback bus; never repeats an effect."""
        return await perform(generation,request_id,lambda:service.verify(operation_id))
    return app

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--music-root',type=Path);a=p.parse_args();logging.disable(logging.CRITICAL)
    bus=os.environ.get('ME3_MUSIC_BUS');server(a.music_root,bus).run(transport='stdio')
