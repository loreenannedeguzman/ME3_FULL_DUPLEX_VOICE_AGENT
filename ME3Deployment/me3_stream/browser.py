"""One session-owned visible Chromium player and its final-output PCM feed.

No API credentials, login/profile reuse, stream URL extraction, system audio
defaults or model-supplied JavaScript. CDP controls only this new browser group.
"""
import asyncio,contextlib,hashlib,json,os,re,secrets,shutil,signal,tempfile,time,uuid
from pathlib import Path
from urllib.parse import urlencode
import aiohttp
from aiohttp import web
from .audio import owned_media_command,stop_pcm_capture,VirtualSink,PCMInput
from .service import StreamError,track_url,snapshot,verify,digest,identity
from .player_page import HTML,CSS,JS
from .discovery import SEARCH_JS

class CDP:
 def __init__(self,ws):self.ws=ws;self.sequence=0;self.lock=asyncio.Lock()
 async def call(self,method,params=None):
  async with self.lock:
   self.sequence+=1;id=self.sequence;await self.ws.send_json(dict(id=id,method=method,params=params or {}))
   async with asyncio.timeout(12):
    async for message in self.ws:
     if message.type!=aiohttp.WSMsgType.TEXT:raise StreamError('owned_browser_disconnected')
     data=json.loads(message.data)
     if data.get('id')!=id:continue
     if 'error' in data:raise StreamError('owned_browser_command_failed')
     return data.get('result',{})
   raise StreamError('owned_browser_disconnected')
 async def evaluate(self,expression):
  value=await self.call('Runtime.evaluate',dict(expression=expression,awaitPromise=True,returnByValue=True))
  if value.get('exceptionDetails'):
   # Retain only a bounded exception class, never page text, URLs or stack.
   detail=value['exceptionDetails'];description=detail.get('exception',{}).get('className');code=description if description in {'TypeError','ReferenceError','SyntaxError','Error'} else 'ScriptError'
   raise StreamError('owned_player_script_failed_'+code)
  return value.get('result',{}).get('value')

class Broker:
 def __init__(self,root,*,pcm,reference_ready,voice_active,current,events,ensure_output=None):
  self.root=Path(root).resolve();self.owner=uuid.uuid4().hex;self.token=secrets.token_hex(32)
  self.path=self.root/'.tmp'/('sc-'+self.owner+'.json');self.socket=self.root/'.tmp'/('sc-'+self.owner+'.sock')
  self.pcm=pcm;self.reference_ready=reference_ready;self.voice_active=voice_active;self.current=current;self.events=events;self.ensure_output=ensure_output
  self.control=None;self.page_server=None;self.origin=None;self.session=None;self.profile=None;self.process=None;self.page=None;self.browser=None
  self.sink=None;self.capture=None;self.reader=None;self.watch=None;self.generation=None;self.sequence=0;self.ops={};self.lock=asyncio.Lock();self.close_lock=asyncio.Lock();self.resources_closed=False;self.closed=False;self.stopped_cleanly=False;self.fault=asyncio.Event();self.cleanup_errors=[];self.playing=False;self.last_verified_command=None
 def emit(self,event,**fields):
  if self.events:self.events.emit(event,**fields)
 def check(self):
  if self.closed or self.current() is not True:raise asyncio.CancelledError()
 def reference_is_ready(self):
  return bool(self.reference_ready() is True and self.reader and not self.reader.done() and self.pcm.fault is None)
 def count(self,name,query='',operation_id=None):
  from me3_assistant.guard import lock
  filename='external-media-actions.jsonl' if operation_id else 'media-search-requests.jsonl';path=self.root/'evidence/assistant'/filename
  with lock(path.with_suffix('.lock')):
   if path.is_symlink():raise StreamError('media_ledger_untrusted')
   rows=[json.loads(r) for r in path.read_bytes().splitlines()] if path.exists() else []
   if any(row.get('version')!=1 or row.get('sequence')!=i+1 for i,row in enumerate(rows)):raise StreamError('media_ledger_invalid')
   row=dict(version=1,sequence=len(rows)+1,at_utc=__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),phase='SoundCloud_broker060',source='SoundCloud visible website/widget',command=name,automatic_retry=False)
   if operation_id:row.update(operation_id=operation_id,state='DISPATCHED',intent_sha256=digest(query))
   else:row.update(action=name,query_sha256=hashlib.sha256(query.encode()).hexdigest())
   fd=os.open(path,os.O_WRONLY|os.O_APPEND|os.O_CREAT|getattr(os,'O_NOFOLLOW',0),0o600)
   with os.fdopen(fd,'w',encoding='utf-8') as f:f.write(json.dumps(row)+'\n');f.flush();os.fsync(f.fileno())
 async def __aenter__(self):
  self.root.joinpath('.tmp').mkdir(exist_ok=True)
  app=web.Application(client_max_size=16384);app.router.add_post('/control',self.handle);self.control=web.AppRunner(app,access_log=None,handler_cancellation=True);await self.control.setup();site=web.UnixSite(self.control,str(self.socket));await site.start();os.chmod(self.socket,0o600)
  fd=os.open(self.path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
  with os.fdopen(fd,'w') as f:json.dump(dict(version=1,owner=self.owner,token=self.token,socket=str(self.socket)),f)
  return self
 async def __aexit__(self,*args):await self.close()
 async def handle(self,request):
  if request.headers.get('Authorization')!='Bearer '+self.token or request.content_type!='application/json':raise web.HTTPForbidden()
  try:
   data=await request.json()
   if not isinstance(data,dict) or set(data)!={'action','owner','payload'} or data['owner']!=self.owner or not isinstance(data['payload'],dict):raise StreamError('owned_broker_message_required')
   action=data['action'];p=data['payload']
   async with self.lock:
    if action=='close_owner':
     if p:raise StreamError('unexpected_close_arguments')
     await self.close_resources();value=dict(closed=self.stopped_cleanly)
    else:
     self.check()
     if action=='search' and set(p)=={'query'}:value=await self.search(p['query'])
     elif action=='inspect' and p=={}:value=await self.inspect()
     elif action=='execute' and set(p)=={'intent','operation_id'}:value=await self.execute(p['intent'],p['operation_id'])
     else:raise StreamError('unsupported_owned_browser_operation')
   return web.json_response(dict(ok=True,result=value))
  except asyncio.CancelledError:raise
  except Exception as exc:
   self.emit('assistant_stream_broker_failed',reason=str(exc)[:120] if isinstance(exc,StreamError) else type(exc).__name__,automatic_retry=False);self.fault.set();return web.json_response(dict(ok=False,error='owned_stream_operation_failed'))
 async def start_browser(self):
  if self.process:
   if self.process.returncode is not None or self.fault.is_set():raise StreamError('owned_stream_browser_lost')
   return
  self.check()
  if self.resources_closed:
   if not self.stopped_cleanly:raise StreamError('stopped_player_cleanup_unverified')
   self.resources_closed=False;self.stopped_cleanly=False
  self.last_verified_command=None
  if self.ensure_output:await self.ensure_output()
  self.sink=VirtualSink();await self.sink.start();self.generation=self.pcm.begin();self.capture=await self.sink.capture();self.reader=asyncio.create_task(self.read_pcm())
  app=web.Application()
  async def asset(request):
   if request.host!=self.origin.removeprefix('http://') or request.headers.get('Sec-Fetch-Site')=='cross-site':raise web.HTTPForbidden()
   name=request.path;value,mime={'/':(HTML,'text/html'),'/player.js':(JS,'text/javascript'),'/style.css':(CSS,'text/css')}[name]
   return web.Response(text=value,content_type=mime,headers={'Cache-Control':'no-store','Referrer-Policy':'strict-origin-when-cross-origin','X-Content-Type-Options':'nosniff','Content-Security-Policy':"default-src 'self'; script-src 'self' https://w.soundcloud.com; frame-src https://w.soundcloud.com; style-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"})
  for path in ['/','/player.js','/style.css']:app.router.add_get(path,asset)
  self.page_server=web.AppRunner(app,access_log=None);await self.page_server.setup();site=web.TCPSite(self.page_server,'127.0.0.1',0);await site.start();self.origin='http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1])
  self.profile=tempfile.TemporaryDirectory(prefix='me3-soundcloud-',dir=self.root/'.tmp');runtime=Path('/run/user')/str(os.getuid())
  env={k:v for k,v in os.environ.items() if k in {'HOME','USER','LOGNAME','PATH','LANG','LC_ALL','LC_CTYPE','DISPLAY','WAYLAND_DISPLAY','XDG_RUNTIME_DIR','DBUS_SESSION_BUS_ADDRESS','XAUTHORITY','XDG_SESSION_TYPE'}}
  env.update(XDG_RUNTIME_DIR=str(runtime),DBUS_SESSION_BUS_ADDRESS='unix:path='+str(runtime/'bus'));env.setdefault('DISPLAY',':0')
  if (runtime/'wayland-0').is_socket():env['WAYLAND_DISPLAY']='wayland-0'
  if (Path.home()/'.Xauthority').is_file():env['XAUTHORITY']=str(Path.home()/'.Xauthority')
  env=self.sink.browser_environment(env);binary=shutil.which('chromium') or shutil.which('chromium-browser')
  if not binary:raise StreamError('Pi_Chromium_required')
  self.emit('assistant_stream_resource_policy',browser_nice_increment=10,monitor_nice_increment=5,media_CPU_policy='last_half_of_parent_allowed_CPUs',parent_CPU_policy_unchanged=True,monitor_threads=1,microphone_frame_ms=20)
  self.process=await asyncio.create_subprocess_exec(*owned_media_command(binary,'--user-data-dir='+self.profile.name,'--no-first-run','--no-default-browser-check','--disable-sync','--disable-extensions','--disable-background-networking','--disable-component-update','--disable-breakpad','--remote-debugging-address=127.0.0.1','--remote-debugging-port=0','--window-size=420,320','--app='+self.origin,priority=10),env=env,stdin=asyncio.subprocess.DEVNULL,stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL,start_new_session=True)
  portfile=Path(self.profile.name)/'DevToolsActivePort';deadline=time.monotonic()+8
  while not portfile.is_file():
   if self.process.returncode is not None or time.monotonic()>deadline:raise StreamError('owned_browser_start_timeout')
   await asyncio.sleep(.1)
  port,path=portfile.read_text().splitlines()[:2];self.port=int(port)
  if not 1024<=self.port<=65535 or not path.startswith('/devtools/browser/'):raise StreamError('owned_browser_debug_binding')
  self.session=aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12),trust_env=False);self.browser=CDP(await self.session.ws_connect('ws://127.0.0.1:'+port+path,max_msg_size=2000000));deadline=time.monotonic()+6
  while True:
   async with self.session.get('http://127.0.0.1:'+port+'/json/list') as response:tabs=await response.json()
   owned=[t for t in tabs if t.get('type')=='page' and t.get('url','').rstrip('/')==self.origin]
   if len(owned)==1:break
   if len(owned)>1 or time.monotonic()>deadline:raise StreamError('owned_visible_target_missing')
   await asyncio.sleep(.1)
  endpoint=owned[0]['webSocketDebuggerUrl']
  if not endpoint.startswith('ws://127.0.0.1:'+port+'/devtools/page/') and not endpoint.startswith('ws://localhost:'+port+'/devtools/page/'):raise StreamError('owned_page_endpoint_invalid')
  self.page=CDP(await self.session.ws_connect(endpoint,max_msg_size=2000000));deadline=time.monotonic()+5
  while not await self.page.evaluate('!!window.me3Media'):
   if time.monotonic()>deadline:raise StreamError('owned_player_shell_not_ready')
   await asyncio.sleep(.1)
  self.watch=asyncio.create_task(self.watch_resources());self.emit('assistant_stream_browser_ready',microphone_opened=False,default_audio_changed=False)
 async def read_pcm(self):
  partial=b''
  try:
   while True:
    chunk=await self.capture.stdout.read(1920)
    if not chunk:raise StreamError('browser_monitor_EOF')
    partial+=chunk;size=len(partial)//4*4
    if size and not self.pcm.offer(partial[:size],self.generation):raise StreamError('browser_PCM_owner_lost')
    partial=partial[size:]
  except asyncio.CancelledError:raise
  except Exception as exc:
   self.fault.set()
   allowed={'media_PCM_overflow','media_PCM_alignment','browser_monitor_EOF','browser_PCM_owner_lost'}
   code=str(exc) if str(exc) in allowed else type(exc).__name__
   with self.pcm.lock:queued=len(self.pcm.buffer);pcm_fault=self.pcm.fault
   self.emit('assistant_stream_audio_failed',stage='monitor_PCM',reason=code,queued_bytes=queued,capacity_bytes=self.pcm.capacity,PCM_fault=pcm_fault,raw_audio_retained=False)
   raise
 async def watch_resources(self):
  try:
   while True:
    reference_lost=self.voice_active() is True and self.reference_ready() is not True
    browser_code=self.process.returncode
    if reference_lost or self.reader.done() or browser_code is not None:
     # A clean user close AFTER independently observed Stop is normal lifecycle.
     # Detach only the stopped external source; never stop local music or voice.
     # Active/uncertain playback loss, abnormal exit and AEC loss remain fatal.
     if not reference_lost and browser_code==0 and self.last_verified_command=='stop' and not self.playing:
      async with self.lock:
       if self.last_verified_command=='stop' and not self.playing and self.current() is True:
        try:
         await self.close_resources(preserve_owner=True)
         self.emit('assistant_stopped_stream_window_retired',reason='clean_window_close_after_verified_stop',owner_preserved=True,automatic_replay=False,voice_stopped=False)
         return
        except Exception:
         self.fault.set();self.emit('assistant_stream_audio_failed',stage='resource_watch',reason='stopped_player_cleanup_unverified');return
     reason='final_AEC_reference_lost' if reference_lost else 'monitor_reader_ended' if self.reader.done() else 'owned_browser_exited'
     self.fault.set();self.emit('assistant_stream_audio_failed',stage='resource_watch',reason=reason,browser_exit_code=browser_code,verified_last_command=self.last_verified_command,playing=self.playing);return
    await asyncio.sleep(.1)
  except asyncio.CancelledError:raise
 async def inspect(self):
  self.sequence+=1
  if not self.page:
   return dict(owner=self.owner,sequence=self.sequence,ready=False,track_url=None,paused=True,position_ms=0,duration_ms=0,volume=40,visible=False,error=None,last_operation_id=None,voice_active=self.voice_active() is True,reference_ready=False)
  raw=await self.page.evaluate('window.me3Media.inspect()');raw.update(owner=self.owner,sequence=self.sequence,voice_active=self.voice_active() is True,reference_ready=self.reference_is_ready())
  value=snapshot(raw,self.owner);self.playing=bool(value['ready'] and not value['paused'] and not value['error']);return value
 async def search(self,query):
  from .service import query_text
  query=query_text(query);await self.start_browser();self.count('search',query)
  target=(await self.browser.call('Target.createTarget',dict(url='about:blank',background=True)))['targetId'];identity(target.lower());ws=None
  try:
   ws=await self.session.ws_connect('ws://127.0.0.1:'+str(self.port)+'/devtools/page/'+target,max_msg_size=2000000);page=CDP(ws)
   nav=await page.call('Page.navigate',dict(url='https://soundcloud.com/search/sounds?'+urlencode(dict(q=query))))
   if nav.get('errorText'):raise StreamError('SoundCloud_search_navigation_failed')
   # One page navigation, bounded UI wait, no private service endpoints.
   deadline=time.monotonic()+10
   expression=SEARCH_JS
   while True:
    self.check();result=await page.evaluate(expression)
    if any(s in result['text'].casefold() for s in ['access denied','verify you are human','current browser isn\'t compatible']):raise StreamError('SoundCloud_search_access_blocked')
    if result['ready'] and result['tracks']:return result['tracks']
    if time.monotonic()>deadline:raise StreamError('SoundCloud_search_no_visible_results')
    await asyncio.sleep(.3)
  finally:
   if ws:await ws.close()
   await self.browser.call('Target.closeTarget',dict(targetId=target))
 async def execute(self,intent,operation_id):
  identity(operation_id);self.check()
  if not isinstance(intent,dict) or intent.get('source')!='SoundCloud' or intent.get('command') not in {'play','pause','resume','stop','volume'}:raise StreamError('unsupported_stream_intent')
  command=intent['command'];allowed={'source','command'}|({'track_url','volume'} if command=='play' else {'volume'} if command=='volume' else set())
  if set(intent)!=allowed:raise StreamError('unexpected_stream_arguments')
  if command=='play':track_url(intent['track_url'])
  if 'volume' in intent and (type(intent['volume']) is not int or not 0<=intent['volume']<=100):raise StreamError('stream_volume_range')
  if operation_id in self.ops:raise StreamError('browser_effect_not_redispatched')
  if len(self.ops)>=128:raise StreamError('owned_stream_operation_limit')
  await self.start_browser();before=await self.inspect()
  if command in {'play','resume'} and not self.reference_is_ready():raise StreamError('owned_PCM_output_not_ready')
  self.ops[operation_id]='UNCERTAIN'
  if command=='play':
   self.count('cue_track',intent['track_url']);await self.page.evaluate('window.me3Media.cue('+json.dumps(intent['track_url'])+')');deadline=time.monotonic()+10
   while True:
    state=await self.inspect()
    if state['error']:raise StreamError('SoundCloud_track_not_available')
    if state['ready'] and state['track_url']==intent['track_url']:break
    if time.monotonic()>deadline:raise StreamError('SoundCloud_track_not_ready')
    await asyncio.sleep(.2)
  await self.page.evaluate('window.me3Media.stage('+json.dumps(intent)+','+json.dumps(operation_id)+')')
  target=await self.page.evaluate("(()=>{const e=document.getElementById('"+command+"'),r=e.getBoundingClientRect(),x=r.x+r.width/2,y=r.y+r.height/2;return {x,y,enabled:!e.disabled,hit:document.elementFromPoint(x,y)===e};})()")
  if not target['enabled'] or not target['hit']:raise StreamError('owned_player_control_not_interactable')
  self.check()
  if command in {'play','resume'} and not self.reference_is_ready():raise StreamError('owned_PCM_output_lost')
  self.count(command,intent,operation_id)
  try:
   await self.page.call('Input.dispatchMouseEvent',dict(type='mousePressed',x=target['x'],y=target['y'],button='left',clickCount=1));await self.page.call('Input.dispatchMouseEvent',dict(type='mouseReleased',x=target['x'],y=target['y'],button='left',clickCount=1));deadline=time.monotonic()+8
   while True:
    state=await self.inspect()
    if state['error']:raise StreamError('SoundCloud_player_failed')
    try:verify(intent,before,state,operation_id);break
    except StreamError:
     if time.monotonic()>deadline:raise
    await asyncio.sleep(.1)
   self.ops[operation_id]='COMPLETED';self.last_verified_command=command;return state
  finally:
   if self.page:
    with contextlib.suppress(Exception):await self.page.evaluate('window.me3Media.clear()')
 async def close_resources(self,*,preserve_owner=False):
  async with self.close_lock:
   if preserve_owner and (self.last_verified_command!='stop' or self.playing or self.current() is not True or self.fault.is_set()):raise StreamError('verified_stopped_player_required')
   if self.resources_closed:
    if not preserve_owner:self.closed=True
    return
   self.playing=False;self.pcm.stop();tasks=[t for t in [self.reader,self.watch] if t and t is not asyncio.current_task()]
   for task in tasks:task.cancel()
   await asyncio.gather(*tasks,return_exceptions=True)
   async def cleanup(name,operation):
    try:await asyncio.wait_for(operation(),6)
    except Exception as exc:self.cleanup_errors.append(dict(resource=name,type=type(exc).__name__))
   if self.session:await cleanup('connection',self.session.close);self.session=None;self.page=None;self.browser=None
   async def browser():
    with contextlib.suppress(ProcessLookupError):os.killpg(self.process.pid,signal.SIGTERM)
    try:await asyncio.wait_for(self.process.wait(),2)
    except TimeoutError:
     with contextlib.suppress(ProcessLookupError):os.killpg(self.process.pid,signal.SIGKILL)
     await asyncio.wait_for(self.process.wait(),1)
    with contextlib.suppress(ProcessLookupError):os.killpg(self.process.pid,signal.SIGKILL)
   if self.process:await cleanup('browser',browser)
   async def capture():await stop_pcm_capture(self.capture)
   if self.capture:await cleanup('capture',capture)
   await asyncio.gather(*tasks,return_exceptions=True)
   if self.sink:await cleanup('sink',self.sink.close)
   if self.page_server:await cleanup('page_server',self.page_server.cleanup);self.page_server=None
   if self.profile and not any(e['resource']=='browser' for e in self.cleanup_errors):
    try:self.profile.cleanup();self.profile=None
    except Exception as exc:self.cleanup_errors.append(dict(resource='profile',type=type(exc).__name__))
   self.stopped_cleanly=not self.cleanup_errors;self.closed=not preserve_owner;self.resources_closed=True
   if preserve_owner:
    if not self.stopped_cleanly:raise StreamError('stopped_player_cleanup_unverified')
    self.process=None;self.capture=None;self.reader=None;self.watch=None;self.sink=None;self.page=None;self.browser=None;self.origin=None;self.generation=None
 async def close(self):
  try:await self.close_resources()
  finally:
   if self.control:await self.control.cleanup();self.control=None
   for path in [self.path,self.socket]:
    if path.exists() or path.is_socket():path.unlink()
