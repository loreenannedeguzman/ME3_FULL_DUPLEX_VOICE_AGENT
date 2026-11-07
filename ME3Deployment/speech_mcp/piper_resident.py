"""Inactive273 candidate: privately settle a canceled, already-issued utterance.

No canceled PCM leaves this adapter. Reuse requires the same exact validated
terminal frame as success; an incomplete drain keeps the existing retire path.
"""
import asyncio,json,os,time,uuid,math
from pathlib import Path
import anyio
from speech_mcp.piper_adapter import PCMConverter
from speech_mcp.tts_adapters import FliteTTS,TTSProviderError,validate_pcm,validate_style
from speech_mcp.contracts import validate_text,identity
WORKER=Path(__file__).with_name('piper_resident_worker.py')

class ResidentPiperTTS(FliteTTS):
    provider='piper';provider_api='local Piper1.8.0;resident framed PCM16 to24000mono'
    cancel_drain_seconds=1.0
    cancel_cleanup_seconds=5.0
    def __init__(self,config):
        super().__init__(config);self.lock=asyncio.Lock();self.epoch=0;self.closed=False;self.metrics={};self.transaction=None
    async def _spawn(self):
        env={k:v for k,v in os.environ.items() if k in {'PATH','SYSTEMROOT','WINDIR','HOME','USERPROFILE','LD_LIBRARY_PATH'}};env['PYTHONUTF8']='1'
        profile=os.getenv('ME3_PIPER_CPU_PROFILE')
        if profile is not None:
            if profile!='duplex_2t_no_spin':raise ValueError('invalid_piper_cpu_profile')
            env.update(ME3_PIPER_CPU_PROFILE=profile,OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1')
        self.epoch+=1
        launch=asyncio.create_task(asyncio.create_subprocess_exec(self.config.piper_python,str(WORKER),self.config.piper_model,*([str(self.config.piper_speaker_id)] if getattr(self.config,'piper_speaker_id',None) is not None else []),stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,env=env,limit=65536))
        try:self.process=await asyncio.shield(launch)
        except asyncio.CancelledError:
            with anyio.CancelScope(shield=True):self.process=await launch
            raise
    async def _retire(self):
        # Caller owns lock: no replacement can exist until this process is reaped.
        await self._stop();self.process=None
    async def _header(self,expected):
        try:line=await self.process.stdout.readline()
        except (ValueError,asyncio.IncompleteReadError):raise ValueError('invalid_piper_frame') from None
        if not line or len(line)>1024:raise ValueError('invalid_piper_frame')
        try:data=json.loads(line)
        except (ValueError,UnicodeError):raise ValueError('invalid_piper_frame') from None
        if not isinstance(data,dict):raise ValueError('invalid_piper_frame')
        if any(type(data.get(k)) is not type(v) or data[k]!=v for k,v in expected.items()):raise ValueError('piper_frame_identity_mismatch')
        return data
    async def synthesize(self,text,style):
        return await self.synthesize_identified(text,style,0,'local')
    async def synthesize_identified(self,text,style,generation,request_id):
        text=validate_text(text);validate_style(style);identity(generation,request_id)
        # A canceled waiter never enters this critical section and cannot kill its owner.
        async with self.lock:
            if self.closed:raise ValueError('piper_worker_closed')
            deadline=asyncio.get_running_loop().time()+self.config.timeout
            state={'issued':False,'retiring':False}
            # A single reader owns even partially received headers/raw frames.
            # Cancellation never starts another reader at an inferred boundary.
            task=asyncio.create_task(self._transaction(text,style,generation,deadline,state));self.transaction=task
            task.add_done_callback(lambda done:None if done.cancelled() else done.exception())
            try:
                pcm,metrics=await asyncio.shield(task)
                self.metrics=metrics
                return pcm
            except asyncio.CancelledError:
                # MCP has already canceled this RPC. Its result must remain
                # canceled even if the private reader reaches a valid end.
                with anyio.CancelScope(shield=True):
                    if state['issued'] and not state['retiring']:
                        await self._wait_transaction(task,min(deadline,asyncio.get_running_loop().time()+self.cancel_drain_seconds))
                    if not task.done() and not state['retiring']:task.cancel()
                    settled=await self._wait_transaction(task,asyncio.get_running_loop().time()+self.cancel_cleanup_seconds)
                    if not settled:
                        self.closed=True
                        raise TTSProviderError('piper_cancel_cleanup_unverified')
                    # Retrieve errors without converting this canceled RPC to
                    # success. _transaction already retires malformed/failed IPC.
                    if not task.cancelled():task.exception()
                raise
            finally:
                if task.done() and self.transaction is task:self.transaction=None
    async def _wait_transaction(self,task,deadline):
        # A repeated caller cancellation must not interrupt owned pipe cleanup.
        while not task.done():
            remaining=deadline-asyncio.get_running_loop().time()
            if remaining<=0:return False
            try:await asyncio.wait({task},timeout=remaining)
            except asyncio.CancelledError:continue
        return True
    async def _transaction(self,text,style,generation,deadline,state):
            started=time.perf_counter();first=None;completed=False
            try:
                async with asyncio.timeout_at(deadline):
                    cold=self.process is None
                    if cold:await self._spawn()
                    if self.process.returncode is not None:raise TTSProviderError('local_tts_process_failed')
                    token=uuid.uuid4().hex;expected={'token':token,'generation':generation,'epoch':self.epoch}
                    self.process.stdin.write(json.dumps({'text':text,'style':style,**expected},ensure_ascii=False).encode('utf-8')+b'\n');state['issued']=True;await self.process.stdin.drain()
                    header=await self._header(expected)
                    if set(header)!=set(expected)|{'kind','sample_rate','channels','sample_width','import_seconds','load_seconds','reuse_index'} or header['kind']!='start':raise ValueError('invalid_piper_frame')
                    for key in ('import_seconds','load_seconds'):
                        if type(header[key]) not in (int,float) or not math.isfinite(header[key]) or header[key]<0:raise ValueError('invalid_piper_frame')
                    if type(header['reuse_index']) is not int or header['reuse_index']<0 or (cold and header['reuse_index']!=0):raise ValueError('invalid_piper_frame')
                    converter=PCMConverter({k:header[k] for k in ('sample_rate','channels','sample_width')});chunks=[];size=0;count=0
                    while True:
                        frame=await self._header(expected)
                        if frame.get('kind')=='end':
                            if set(frame)!=set(expected)|{'kind','byte_count','chunks'} or type(frame['byte_count']) is not int or frame['byte_count']!=size or type(frame['chunks']) is not int or frame['chunks']!=count:raise ValueError('invalid_piper_frame')
                            break
                        if set(frame)!=set(expected)|{'kind','byte_count'} or frame.get('kind')!='pcm' or type(frame['byte_count']) is not int or not 1<=frame['byte_count']<=65536:raise ValueError('invalid_piper_frame')
                        size+=frame['byte_count'];count+=1
                        if size>converter.rate*2*45:raise ValueError('tts_audio_size_limit')
                        chunk=await self.process.stdout.readexactly(frame['byte_count'])
                        if first is None:first=time.perf_counter()-started
                        chunks.append(converter.feed(chunk))
                    converter.finish();pcm=validate_pcm(b''.join(chunks))
                    metrics={'backend_first_pcm_seconds':first,'backend_complete_seconds':time.perf_counter()-started,'native_sample_rate':converter.rate,'worker_pid':self.process.pid,'worker_epoch':self.epoch,'cold_start':cold,'reuse_index':header['reuse_index'],'model_import_seconds':header['import_seconds'],'model_load_seconds':header['load_seconds']}
                    completed=True
                    return pcm,metrics
            except TimeoutError:raise TTSProviderError('tts_timeout') from None
            except (BrokenPipeError,ConnectionError,asyncio.IncompleteReadError):raise TTSProviderError('local_tts_process_failed') from None
            finally:
                # Only a fully validated end frame may leave a worker reusable.
                if not completed:
                    state['retiring']=True
                    try:
                        with anyio.CancelScope(shield=True):await self._retire()
                    except BaseException:self.closed=True;raise
    async def close(self):
        self.closed=True
        with anyio.CancelScope(shield=True):
            async with self.lock:
                if self.transaction is not None:
                    if not await self._wait_transaction(self.transaction,asyncio.get_running_loop().time()+self.cancel_cleanup_seconds):raise TTSProviderError('piper_cancel_cleanup_unverified')
                    if not self.transaction.cancelled():self.transaction.exception()
                    self.transaction=None
                await self._retire()
