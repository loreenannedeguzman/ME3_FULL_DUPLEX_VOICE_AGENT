"""Guarded SoundCloud lifecycle. Search is not playback or availability proof.

The backend operates one owned visible browser and supplies independent getters.
This module does not discover credentials, extract streams or launch a browser.
"""
import asyncio,copy,hashlib,json,math,os,re,secrets,stat,time,uuid
from pathlib import Path
from urllib.parse import urlsplit

class StreamError(ValueError):pass
COMMANDS={'play','pause','resume','stop','volume'}
def identity(value):
 if not isinstance(value,str) or not re.fullmatch('[0-9a-f]{32}',value):raise StreamError('invalid_operation_identity')
 return value
def track_url(value):
 if not isinstance(value,str) or len(value)>300:raise StreamError('public_SoundCloud_track_required')
 u=urlsplit(value)
 if u.scheme!='https' or u.netloc!='soundcloud.com' or u.query or u.fragment or not re.fullmatch('/[A-Za-z0-9_-]{1,128}/[A-Za-z0-9_-]{1,128}',u.path):raise StreamError('public_SoundCloud_track_required')
 if u.path.split('/')[1].casefold() in {'search','you','settings','discover','stream','pages','terms-of-use','charts','upload','connect','oauth','topics'}:raise StreamError('track_resource_required')
 return value
def query_text(value):
 if not isinstance(value,str) or not 1<=len(value.strip())<=120 or any(ord(c)<32 for c in value):raise StreamError('invalid_music_query')
 return value.strip()
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def snapshot(value,owner):
 if not isinstance(value,dict):raise StreamError('player_snapshot_required')
 keys={'owner','sequence','ready','track_url','paused','position_ms','duration_ms','volume','visible','error','last_operation_id','voice_active','reference_ready'}
 if set(value)!=keys or value['owner']!=owner:raise StreamError('player_owner_or_schema_mismatch')
 if type(value['sequence']) is not int or value['sequence']<1:raise StreamError('fresh_player_sequence_required')
 for name in ['ready','paused','visible','voice_active','reference_ready']:
  if type(value[name]) is not bool:raise StreamError('player_boolean_required')
 if type(value['volume']) is not int or not 0<=value['volume']<=100:raise StreamError('player_volume_invalid')
 for name in ['position_ms','duration_ms']:
  if type(value[name]) not in {int,float} or not math.isfinite(value[name]) or not 0<=value[name]<=864000000:raise StreamError('player_position_invalid')
 if value['track_url'] is not None:track_url(value['track_url'])
 if value['last_operation_id'] is not None:identity(value['last_operation_id'])
 if value['error'] not in {None,'unavailable','region_restricted','login_required','autoplay_blocked','hidden','owner_lost','network','player_error'}:raise StreamError('player_error_invalid')
 return copy.deepcopy(value)
def verify(intent,before,after,operation_id):
 if after['sequence']<=before['sequence'] or after['last_operation_id']!=operation_id:raise StreamError('independent_command_readback_required')
 command=intent['command']
 if after['error'] or not after['ready']:raise StreamError('player_not_ready_or_failed')
 if command not in {'stop','pause'} and not after['visible']:raise StreamError('visible_player_required')
 expected=intent.get('track_url',before['track_url'])
 if command!='stop' and after['track_url']!=expected:raise StreamError('different_track_after_control')
 if command in {'play','resume'} and (after['paused'] or after['voice_active'] and not after['reference_ready']):raise StreamError('playing_reference_not_verified')
 if command in {'pause','stop'} and not after['paused']:raise StreamError('pause_not_verified')
 if command=='stop' and after['position_ms']>250:raise StreamError('stop_position_not_verified')
 if 'volume' in intent and after['volume']!=intent['volume']:raise StreamError('volume_readback_mismatch')
 return True

class Store:
 """Append-only effect identities; a restart never replays DISPATCHED work."""
 def __init__(self,path,root,owner):
  self.path=Path(path);self.root=Path(root).resolve();self.owner=identity(owner);self.rows={};self.sequence=0
  self.lease=None
  if not self.path.resolve().is_relative_to(self.root):raise StreamError('stream_store_outside_project')
  if self.path.is_symlink():raise StreamError('untrusted_stream_store')
  from me3_assistant.guard import lock
  self.path.parent.mkdir(parents=True,exist_ok=True)
  lease=self.path.with_suffix('.lease')
  if lease.is_symlink():raise StreamError('untrusted_stream_lease')
  self.lease=lock(lease);self.lease.__enter__()
  try:self.load()
  except BaseException:self.close();raise
 def load(self):
  if self.path.exists():
   info=self.path.stat()
   if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_size>2000000:raise StreamError('untrusted_stream_store')
   for line in self.path.read_text().splitlines():
    row=json.loads(line);self.sequence+=1
    if row['sequence']!=self.sequence or row['owner']!=self.owner or row['state'] not in {'DISPATCHED','COMPLETED','UNCERTAIN'}:raise StreamError('stream_store_invalid')
    identity(row['operation_id']);old=self.rows.get(row['operation_id'])
    if old and any(old[k]!=row[k] for k in ['intent','expected_digest','token_sha256']):raise StreamError('stream_operation_changed')
    if row['expected_digest']!=digest(row['intent']):raise StreamError('stream_digest_mismatch')
    self.rows[row['operation_id']]=row
 def close(self):
  if self.lease:self.lease.__exit__(None,None,None);self.lease=None
 def append(self,row,state,sequence=None):
  if state not in {'DISPATCHED','COMPLETED','UNCERTAIN'}:raise StreamError('invalid_stream_state')
  self.path.parent.mkdir(parents=True,exist_ok=True)
  if self.path.is_symlink() or self.path.exists() and self.path.stat().st_size>2000000:raise StreamError('untrusted_stream_store')
  value={**row,'state':state,'owner':self.owner,'sequence':self.sequence+1}
  if sequence is not None:value['ack_sequence']=sequence
  fd=os.open(self.path,os.O_WRONLY|os.O_APPEND|os.O_CREAT|getattr(os,'O_NOFOLLOW',0),0o600)
  with os.fdopen(fd,'w',encoding='utf-8') as f:f.write(json.dumps(value,separators=(',',':'))+'\n');f.flush();os.fsync(f.fileno())
  self.sequence+=1;self.rows[value['operation_id']]=value;return value

class Service:
 def __init__(self,backend,owner,store,*,clock=time.monotonic):
  self.backend=backend;self.owner=identity(owner);self.store=store;self.clock=clock;self.selections={};self.pending={};self.lock=asyncio.Lock()
 async def search(self,query):
  query=query_text(query);found=await self.backend.search(query)
  if not isinstance(found,list) or len(found)>5:raise StreamError('bounded_search_result_required')
  now=self.clock();selections={k:v for k,v in self.selections.items() if v['expires']>now};results=[]
  by_url={v['track_url']:k for k,v in selections.items()}
  for item in found:
   if not isinstance(item,dict) or set(item)!={'title','uploader','url'}:raise StreamError('search_result_schema')
   for field in ['title','uploader']:
    if not isinstance(item[field],str) or not 1<=len(item[field])<=200 or any(ord(c)<32 for c in item[field]):raise StreamError('invalid_search_metadata')
   url=track_url(item['url']);key=by_url.get(url) or uuid.uuid4().hex
   selections.pop(key,None);selections[key]=dict(track_url=url,title=item['title'],uploader=item['uploader'],expires=now+600);by_url[url]=key
   results.append(dict(selection_id=key,**item,playability_verified=False))
  # Recent discovered identities remain usable for corrections, but every
  # old uncommitted proposal is invalidated. Generation/owner/effect guards stay.
  self.selections=dict(list(selections.items())[-15:]);self.pending.clear()
  return dict(source='SoundCloud public website',query=query,tracks=results,playback_started=False,content_is_untrusted=True)
 async def inspect(self):return snapshot(await self.backend.inspect(),self.owner)
 async def prepare(self,command,operation_id,selection_id=None,volume=None):
  identity(operation_id)
  if command not in COMMANDS:raise StreamError('unsupported_stream_control')
  if operation_id in self.store.rows or operation_id in self.pending:raise StreamError('stream_operation_already_exists')
  if len(self.pending)>=128:raise StreamError('pending_stream_operation_limit')
  clean=dict(command=command,source='SoundCloud')
  if command=='play':
   selected=self.selections.get(selection_id)
   if not selected or selected['expires']<self.clock():raise StreamError('fresh_discovered_selection_required')
   clean['track_url']=selected['track_url']
  elif selection_id is not None:raise StreamError('unexpected_stream_selection')
  if command in {'play','volume'}:
   volume=40 if command=='play' and volume is None else volume
   if type(volume) is not int or not 0<=volume<=100:raise StreamError('volume_range_zero_to_100')
   clean['volume']=volume
  elif volume is not None:raise StreamError('unexpected_stream_volume')
  token=secrets.token_hex(16);row=dict(operation_id=operation_id,intent=clean,expected_digest=digest(clean),token_sha256=hashlib.sha256(token.encode()).hexdigest(),expires=self.clock()+120)
  self.pending[operation_id]=row
  return dict(operation_id=operation_id,token=token,expected_digest=row['expected_digest'],**clean)
 async def commit(self,operation_id,token,confirmation):
  identity(operation_id)
  if not isinstance(token,str) or confirmation!='confirm':raise StreamError('stream_confirmation_required')
  async with self.lock:
   prior=self.store.rows.get(operation_id);row=prior or self.pending.get(operation_id)
   if not row or not secrets.compare_digest(row['token_sha256'],hashlib.sha256(token.encode()).hexdigest()):raise StreamError('stream_confirmation_required')
   if prior:
    if prior['state']!='COMPLETED':raise StreamError('uncertain_stream_effect_not_retried')
    return dict(operation_id=operation_id,duplicate=True)
   if row['expires']<self.clock():raise StreamError('stream_proposal_expired')
   before=await self.inspect();c=row['intent']
   if c['command'] in {'play','resume'} and (not before['ready'] or not before['visible'] or before['voice_active'] and not before['reference_ready']):raise StreamError('stream_audio_or_visibility_not_ready')
   # Persist immediately before dispatch. No uncertain effect is ever resent.
   row={k:v for k,v in row.items() if k!='expires'};row['track_before']=before['track_url'];self.store.append(row,'DISPATCHED');del self.pending[operation_id]
   try:
    ack=snapshot(await self.backend.execute(c,operation_id),self.owner);verify(c,before,ack,operation_id)
    self.store.append(row,'COMPLETED',ack['sequence']);return dict(operation_id=operation_id,duplicate=False)
   except BaseException:self.store.append(row,'UNCERTAIN');raise
 async def read_operation(self,operation_id):
  identity(operation_id);row=self.store.rows.get(operation_id)
  return dict(operation_id=operation_id,row={k:v for k,v in row.items() if k!='token_sha256'} if row else None,snapshot=await self.inspect(),verification_source='fresh_SoundCloud_widget_getters')
 async def close(self):
  try:await self.backend.close()
  finally:self.store.close()
