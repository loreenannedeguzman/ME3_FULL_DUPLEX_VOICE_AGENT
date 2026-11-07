"""Owned temporary virtual sink and PCM input; NO production voice enablement.

Never changes defaults, loads persistent modules, records a microphone, reads
unrelated desktop streams, extracts a service stream or opens physical output.
The caller must join this PCM BEFORE ME3's existing final-output AEC wrapper.
"""
import array,asyncio,json,os,re,shutil,sys,threading,time,uuid
from collections import deque
from pathlib import Path

class MediaAudioError(ValueError):pass
def owned_media_command(executable,*arguments,priority=10):
 # Lower only our media child and its descendants. Parent capture/audio policy,
 # desktop browsers, system defaults and all machine settings stay untouched.
 if sys.platform!='linux' or type(priority) is not int or priority not in (5,10):raise MediaAudioError('owned_media_resource_policy_required')
 nice=shutil.which('nice');taskset=shutil.which('taskset')
 if not nice or not taskset or not hasattr(os,'sched_getaffinity'):raise MediaAudioError('owned_media_resource_tools_required')
 allowed=sorted(os.sched_getaffinity(0))
 if not allowed:raise MediaAudioError('owned_media_CPU_set_required')
 selected=allowed[-max(1,len(allowed)//2):]
 return [nice,'-n',str(priority),taskset,'-c',','.join(map(str,selected)),executable,*arguments]
async def stop_pcm_capture(process):
 # Awaiting process.wait while its unread PCM pipe is full can deadlock even
 # after termination. The only reader is canceled/joined before this discard.
 async def discard():
  if process.stdout is not None:
   while await process.stdout.read(16384):pass
 drain=asyncio.create_task(discard())
 try:
  if process.returncode is None:
   try:process.terminate()
   except ProcessLookupError:pass
  try:await asyncio.wait_for(process.wait(),1)
  except TimeoutError:
   if process.returncode is None:
    try:process.kill()
    except ProcessLookupError:pass
   await asyncio.wait_for(process.wait(),1)
  await asyncio.wait_for(asyncio.shield(drain),1)
 finally:
  if not drain.done():drain.cancel()
  await asyncio.gather(drain,return_exceptions=True)

async def command(*args):
 p=await asyncio.create_subprocess_exec(*args,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
 try:out,err=await asyncio.wait_for(p.communicate(),5)
 except BaseException:
  if p.returncode is None:p.kill()
  await p.wait();raise
 if p.returncode or len(out)>2_000_000:raise MediaAudioError('audio_introspection_failed')
 return out
async def graph():return json.loads(await command('/usr/bin/pw-dump'))
async def defaults():
 # Independent observation only; no set-default, volume or routing mutation.
 return [await command('/usr/bin/wpctl','inspect',n) for n in ['@DEFAULT_AUDIO_SINK@','@DEFAULT_AUDIO_SOURCE@']]
def node_by_name(rows,name):
 matches=[r for r in rows if r.get('type')=='PipeWire:Interface:Node' and r.get('info',{}).get('props',{}).get('node.name')==name]
 if len(matches)>1:raise MediaAudioError('ambiguous_owned_sink')
 return matches[0] if matches else None
async def settle_gone(name):
 end=time.monotonic()+2
 while node_by_name(await graph(),name):
  if time.monotonic()>end:raise MediaAudioError('owned_virtual_sink_cleanup_incomplete')
  await asyncio.sleep(.03)
class VirtualSink:
 def __init__(self):self.name='me3_media_'+uuid.uuid4().hex;self.process=None;self.before=None;self.node=None
 async def start(self):
  if sys.platform!='linux' or any(not shutil.which(n) for n in ['pw-cli','pw-dump','wpctl','ffmpeg']):raise MediaAudioError('Pi_user_audio_runtime_required')
  runtime=Path(os.environ.get('XDG_RUNTIME_DIR','/nonexistent'))
  if not runtime.is_dir() or runtime.stat().st_uid!=os.getuid() or not (runtime/'pulse/native').is_socket():raise MediaAudioError('owned_user_audio_session_required')
  self.before=await defaults()
  assert node_by_name(await graph(),self.name) is None
  self.process=await asyncio.create_subprocess_exec('/usr/bin/pw-cli',stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
  line='create-node adapter { factory.name = support.null-audio-sink node.name = '+self.name+' node.description = ME3_owned_media media.class = Audio/Sink audio.position = [ FL FR ] object.linger = false priority.session = 0 }\n'
  try:
   self.process.stdin.write(line.encode());await self.process.stdin.drain();end=time.monotonic()+4
   while True:
    self.node=node_by_name(await graph(),self.name)
    if self.node:break
    if self.process.returncode is not None or time.monotonic()>end:raise MediaAudioError('owned_virtual_sink_start_failed')
    await asyncio.sleep(.04)
   if self.before!=await defaults():raise MediaAudioError('default_audio_changed_during_sink_start')
   return self
  except BaseException:
   await self.close();raise
 def browser_environment(self,base):
  if not self.node or self.process.returncode is not None:raise MediaAudioError('owned_sink_not_ready')
  # Applies to a NEW, project-owned browser process only, never the desktop.
  return {**base,'PULSE_SINK':self.name}
 async def capture(self):
  if not self.node or self.process.returncode is not None:raise MediaAudioError('owned_sink_not_ready')
  args=owned_media_command('/usr/bin/ffmpeg','-nostdin','-loglevel','error','-threads','1','-filter_threads','1','-f','pulse','-sample_rate','48000','-channels','2','-i',self.name+'.monitor','-threads','1','-acodec','pcm_s16le','-ar','48000','-ac','2','-f','s16le','pipe:1',priority=5)
  return await asyncio.create_subprocess_exec(*args,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
 async def close(self):
  if self.process:
   if self.process.returncode is None:
    try:self.process.stdin.write(b'quit\n');await self.process.stdin.drain()
    except (BrokenPipeError,ConnectionResetError):pass
    self.process.stdin.close()
    try:await asyncio.wait_for(self.process.wait(),2)
    except TimeoutError:
     self.process.terminate()
     try:await asyncio.wait_for(self.process.wait(),1)
     except TimeoutError:self.process.kill();await asyncio.wait_for(self.process.wait(),1)
   await settle_gone(self.name);self.process=None;self.node=None
  if self.before is not None and self.before!=await defaults():raise MediaAudioError('default_audio_changed')

class PCMInput:
 """Bounded in-memory audio; cancellation clears queued samples immediately."""
 def __init__(self,capacity=19200):
  if type(capacity) is not int or capacity<3840 or capacity>76800 or capacity%4:raise MediaAudioError('invalid_PCM_capacity')
  self.capacity=capacity;self.buffer=bytearray();self.generation=0;self.active=False;self.fault=None;self.lock=threading.Lock()
 def begin(self):
  with self.lock:self.generation+=1;self.buffer.clear();self.active=True;self.fault=None;return self.generation
 def stop(self):
  with self.lock:self.generation+=1;self.active=False;self.buffer.clear()
 def offer(self,pcm,generation):
  if not isinstance(pcm,bytes) or len(pcm)%4:raise MediaAudioError('media_PCM_alignment')
  with self.lock:
   if not self.active or generation!=self.generation:return False
   if len(self.buffer)+len(pcm)>self.capacity:self.active=False;self.buffer.clear();self.fault='media_PCM_overflow';raise MediaAudioError(self.fault)
   self.buffer.extend(pcm);return True
 def mix(self,voice,user_speaking=False):
  if not isinstance(voice,bytes) or len(voice)%4:raise MediaAudioError('voice_PCM_alignment')
  if not self.lock.acquire(False):return voice
  try:
   if not self.active:return voice
   count=min(len(voice),len(self.buffer));raw=bytes(self.buffer[:count]);del self.buffer[:count]
  finally:self.lock.release()
  gain=0 if user_speaking else .15 if any(voice) else 1
  if not gain or not raw:return voice
  a=array.array('h',voice);b=array.array('h',raw)
  if sys.byteorder!='little':a.byteswap();b.byteswap()
  for i,n in enumerate(b):a[i]=max(-32768,min(32767,a[i]+round(n*gain)))
  if sys.byteorder!='little':a.byteswap()
  return a.tobytes()

def attach_before_echo(result,settings,pcm_input,*,echo_not_yet_attached):
 if echo_not_yet_attached is not True or settings.output_rate!=48000 or settings.output_channels!=2:raise MediaAudioError('media_must_precede_final_AEC_reference')
 _,_,transport,processor=result;output=transport.output();original=output.callback
 def callback(*args):
  voice,flag=original(*args)
  return pcm_input.mix(voice),flag
 output.callback=callback
