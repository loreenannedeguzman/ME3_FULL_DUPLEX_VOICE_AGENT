"""Official YouTube metadata and a visible-player bridge; no media extraction."""
import asyncio,hashlib,json,os,re,secrets,stat,time,uuid
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
import aiohttp
from task_mcp.client import ROOT

COMMANDS={'play','pause','resume','stop','volume','inspect'}
def video_id(value):
    if not isinstance(value,str) or not 1<=len(value)<=500 or any(ord(c)<32 for c in value):raise ValueError('youtube_link_required')
    if re.fullmatch(r'[A-Za-z0-9_-]{11}',value):return value
    u=urlsplit(value)
    try:port=u.port
    except ValueError:raise ValueError('invalid_youtube_link') from None
    if u.scheme!='https' or port not in (None,443) or u.username or u.password:raise ValueError('invalid_youtube_link')
    if u.hostname=='youtu.be':result=u.path.lstrip('/')
    elif u.hostname in {'youtube.com','www.youtube.com','m.youtube.com','music.youtube.com'}:
        if u.path=='/watch':result=parse_qs(u.query).get('v',[''])[0]
        elif re.fullmatch(r'/(?:shorts|embed)/[A-Za-z0-9_-]{11}',u.path):result=u.path.split('/')[-1]
        else:raise ValueError('invalid_youtube_link')
    else:raise ValueError('invalid_youtube_link')
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}',result):raise ValueError('invalid_youtube_link')
    return result

def intent(command,video=None,volume=None):
    if command not in COMMANDS-{'inspect'}:raise ValueError('unsupported_youtube_control')
    result={'command':command}
    if command=='play':result['video_id']=video_id(video)
    elif video is not None:raise ValueError('unexpected_youtube_video')
    if command in {'play','volume'}:
        volume=33 if command=='play' and volume is None else volume
        if type(volume) is float and volume.is_integer():volume=int(volume)
        if type(volume) is not int or not 0<=volume<=100:raise ValueError('youtube_volume_range')
        result['volume']=volume
    elif volume is not None:raise ValueError('unexpected_youtube_volume')
    return result

def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()

class PlayerBridge:
    """Owner-loop command/observation broker. Browser reports never prove audibility."""
    def __init__(self,voice_active=lambda:False,clock=time.monotonic):
        self.voice_active=voice_active;self.clock=clock;self.client=None;self.seen=0;self.pending=None;self.waiter=None;self.owner=None;self.operations={};self.lock=asyncio.Lock();self.latest=dict(state='unavailable',video_id=None,volume=0,visible=False,error=None);self.sequence=0
    def public(self):return {**self.latest,'fresh':self.client is not None and self.clock()-self.seen<3,'sequence':self.sequence,'voice_coexistence_qualified':False}
    def poll(self,client):
        if not isinstance(client,str) or not re.fullmatch('[0-9a-f]{32}',client):raise ValueError('invalid_youtube_client')
        if self.client is not None and client!=self.client and self.clock()-self.seen>=3 and self.pending is None:
            self.client=None;self.owner=None;self.latest=dict(state='unavailable',video_id=None,volume=0,visible=False,error=None)
        if self.client is None:self.client=client
        if client!=self.client:raise ValueError('youtube_already_open_in_another_tab')
        self.seen=self.clock()
        return {'command':self.pending,'player':self.public(),'voice_active':bool(self.voice_active())}
    def report(self,client,packet):
        if client!=self.client or not isinstance(packet,dict):raise ValueError('invalid_youtube_report')
        state=packet.get('state');vid=packet.get('video_id');volume=packet.get('volume');visible=packet.get('visible');error=packet.get('error')
        if state not in {'unavailable','unstarted','cued','playing','paused','stopped','ended','buffering','blocked','error'} or vid is not None and not re.fullmatch(r'[A-Za-z0-9_-]{11}',vid) or type(volume) is not int or not 0<=volume<=100 or type(visible) is not bool or error is not None and error not in {'autoplay_blocked','player_unavailable','hidden','voice_audio_unqualified','unsupported_video','network','player_error'}:raise ValueError('invalid_youtube_report')
        self.latest=dict(state=state,video_id=vid,volume=volume,visible=visible,error=error);self.seen=self.clock();self.sequence+=1
        if self.pending and packet.get('command_id')==self.pending['id'] and self.waiter and not self.waiter.done():self.waiter.set_result(self.public())
        return {'ok':True}
    async def execute(self,clean,owner,operation_id,timeout=10):
        if clean['command'] not in COMMANDS:raise ValueError('unsupported_youtube_control')
        if clean['command'] in {'play','resume'} and self.voice_active():raise ValueError('youtube_voice_audio_not_qualified_use_text')
        async with self.lock:
            if operation_id in self.operations:return self.operations[operation_id]
            if len(self.operations)>=128:raise ValueError('youtube_operation_limit')
            if not self.public()['fresh']:raise ValueError('open_ME3_YouTube_player_first')
            self.pending=dict(id=uuid.uuid4().hex,**clean);self.waiter=asyncio.get_running_loop().create_future();self.operations[operation_id]={'state':'UNCERTAIN','operation_id':operation_id}
            try:
                async with asyncio.timeout(timeout):result=await self.waiter
                if result['error'] or clean['command']!='stop' and not result['visible']:raise ValueError('youtube_player_unavailable_or_blocked')
                expected={'play':{'playing'},'pause':{'paused','ended'},'resume':{'playing'},'stop':{'stopped','unstarted','cued','paused','ended'},'volume':{'playing','paused','cued','ended'},'inspect':{result['state']}}[clean['command']]
                if result['state'] not in expected or clean['command']=='play' and result['video_id']!=clean['video_id'] or 'volume' in clean and result['volume']!=clean['volume']:raise ValueError('youtube_control_not_verified')
                if clean['command']!='inspect':self.owner=owner
                self.operations[operation_id]=dict(state='COMPLETED',operation_id=operation_id,snapshot=result)
                return self.operations[operation_id]
            finally:self.pending=None;self.waiter=None
    async def stop_owner(self,owner):
        if self.owner!=owner:return {'stopped':False,'reason':'different_owner'}
        result=await self.execute({'command':'stop'},owner,uuid.uuid4().hex);self.owner=None;return result

class BridgeClient:
    def __init__(self,path=None):self.path=Path(path or ROOT/'.tmp/youtube-bridge.json')
    async def call(self,action,**data):
        p=self.path
        if p.is_symlink() or not p.is_file() or p.stat().st_size>2048:raise ValueError('youtube_GUI_bridge_unavailable')
        if os.name!='nt' and (p.stat().st_uid!=os.getuid() or stat.S_IMODE(p.stat().st_mode)&0o077):raise ValueError('youtube_bridge_permissions')
        config=json.loads(p.read_bytes());port=config['port'];token=config['token']
        if type(port) is not int or not 1024<=port<=65535 or not re.fullmatch('[0-9a-f]{64}',token):raise ValueError('invalid_youtube_bridge')
        async with aiohttp.ClientSession(trust_env=False,timeout=aiohttp.ClientTimeout(total=13)) as session:
            async with session.post(f'http://127.0.0.1:{port}/api/youtube-bridge',headers={'Authorization':'Bearer '+token},json={'action':action,**data},allow_redirects=False) as response:
                raw=await response.content.read(32769)
                if response.status!=200 or len(raw)>32768:raise ValueError('youtube_bridge_request_failed')
                result=json.loads(raw)
                if not isinstance(result,dict):raise ValueError('invalid_youtube_bridge_result')
                return result

async def metadata_request(url,params,key=None):
    # Fixed official JSON endpoints only; never retrieve audio/video streams.
    if url not in {'https://www.youtube.com/oembed','https://www.googleapis.com/youtube/v3/search'}:raise ValueError('unapproved_youtube_endpoint')
    from me3_assistant.guard import lock
    from benchmark_m6.storage import atomic
    counter=ROOT/'evidence/assistant/youtube-requests.json'
    with lock(counter.with_suffix('.lock')):
        if counter.is_symlink():raise ValueError('youtube_counter_untrusted')
        data=json.loads(counter.read_bytes()) if counter.exists() else {'version':1,'attempts':0}
        if set(data)!={'version','attempts'} or data['version']!=1 or type(data['attempts']) is not int or data['attempts']<0:raise ValueError('invalid_youtube_counter')
        data['attempts']+=1;atomic(counter,data)
    async with aiohttp.ClientSession(trust_env=True,timeout=aiohttp.ClientTimeout(total=12)) as session:
        async with session.get(url,params=params,headers={'X-Goog-Api-Key':key} if key else {},allow_redirects=False) as response:
            raw=await response.content.read(131073)
            if response.status!=200:raise ValueError('youtube_metadata_service_unavailable')
            if len(raw)>131072:raise ValueError('youtube_metadata_too_large')
            return json.loads(raw)

async def read_video(value,request=metadata_request):
    vid=video_id(value);data=await request('https://www.youtube.com/oembed',{'url':'https://www.youtube.com/watch?v='+vid,'format':'json'})
    return dict(video_id=vid,url='https://www.youtube.com/watch?v='+vid,title=str(data.get('title',''))[:300],channel=str(data.get('author_name',''))[:200],source='YouTube oEmbed',content_obtained='metadata_only',playback_started=False)

async def search(query,limit=5,request=metadata_request,key_path=None):
    if not isinstance(query,str) or not 1<=len(query.strip())<=120 or any(ord(c)<32 for c in query) or type(limit) is not int or not 1<=limit<=5:raise ValueError('invalid_youtube_search')
    p=Path(key_path or ROOT/'.env.youtube')
    if p.is_symlink() or not p.is_file() or p.stat().st_size>2048:return dict(available=False,reason='YouTube Data API v3 key is not configured; a YouTube link can still be opened.',videos=[],playback_started=False)
    if os.name!='nt' and (p.stat().st_uid!=os.getuid() or stat.S_IMODE(p.stat().st_mode)&0o077):raise ValueError('youtube_key_file_permissions')
    from dotenv import dotenv_values
    key=dotenv_values(p,interpolate=False).get('YOUTUBE_API_KEY')
    if not isinstance(key,str) or not re.fullmatch('[A-Za-z0-9_-]{20,200}',key):raise ValueError('youtube_key_not_configured')
    data=await request('https://www.googleapis.com/youtube/v3/search',dict(part='snippet',q=query.strip(),type='video',videoEmbeddable='true',maxResults=limit,safeSearch='moderate'),key=key)
    videos=[]
    for item in data.get('items',[])[:limit]:
        try:vid=video_id(item['id']['videoId'])
        except (ValueError,KeyError,TypeError):continue
        snippet=item.get('snippet',{});videos.append(dict(video_id=vid,url='https://www.youtube.com/watch?v='+vid,title=str(snippet.get('title',''))[:300],channel=str(snippet.get('channelTitle',''))[:200]))
    return dict(available=True,query=query.strip(),videos=videos,source='YouTube Data API v3',playback_started=False)
