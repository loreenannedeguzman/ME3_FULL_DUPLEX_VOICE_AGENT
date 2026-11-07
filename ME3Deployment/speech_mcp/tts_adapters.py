"""All backends return bounded24kHz mono PCM16. No implicit fallback/retry."""
import asyncio,json,os,tempfile,re,time
from pathlib import Path
import httpx
from speech_mcp.contracts import validate_text,MAX_TTS_SECONDS
from speech_mcp.provider import GoogleSpeech

ROOT=Path(__file__).resolve().parents[1]
MAX_PCM_BYTES=24000*2*MAX_TTS_SECONDS

class TTSProviderError(ValueError):
    def __init__(self,reason,code=None):
        super().__init__(reason);self.safe_reason=reason;self.code=code

def validate_pcm(pcm):
    if not isinstance(pcm,bytes) or not pcm or len(pcm)%2 or len(pcm)>MAX_PCM_BYTES or pcm.startswith(b'RIFF'):
        raise ValueError('invalid_tts_pcm')
    return pcm

def validate_style(style):
    if style not in {'normal','slow'}:raise ValueError('invalid_style')

class ElevenBudget:
    """Single-agent, timestamp-free approved request/character reservations."""
    def __init__(self,config,folder):self.config=config;self.path=Path(folder)/('elevenlabs-'+config.budget_id+'.json')
    def reserve(self,characters):
        state={'requests':0,'characters':0}
        if self.path.exists():
            if self.path.stat().st_size>1024:raise ValueError('invalid_tts_budget_state')
            try:state=json.loads(self.path.read_text(encoding='utf-8'))
            except (ValueError,UnicodeError):raise ValueError('invalid_tts_budget_state') from None
            if not isinstance(state,dict) or set(state)!={'requests','characters'} or any(type(x) is not int or x<0 for x in state.values()):raise ValueError('invalid_tts_budget_state')
        if state['requests']+1>self.config.request_budget or state['characters']+characters>self.config.character_budget:raise TTSProviderError('tts_request_budget_exhausted')
        self.path.parent.mkdir(parents=True,exist_ok=True)
        state={'requests':state['requests']+1,'characters':state['characters']+characters}
        temporary=self.path.with_suffix('.'+str(os.getpid())+'.tmp')
        temporary.write_text(json.dumps(state),encoding='utf-8');os.replace(temporary,self.path)

class GoogleTTS:
    provider='google';provider_api='generateContent'
    def __init__(self,config):self.config=config;self.delegate=GoogleSpeech();self.source_mime=''
    async def synthesize(self,text,style):
        validate_style(style)
        pcm=await self.delegate.synthesize(validate_text(text),style)
        self.source_mime=self.delegate.source_mime
        return validate_pcm(pcm)
    async def close(self):await self.delegate.close()

class ElevenTTS:
    provider='elevenlabs';provider_api='POST /v1/text-to-speech;pcm_24000'
    def __init__(self,config,*,client=None,budget=None):
        config.require_ready();self.config=config;self.source_mime='audio/L16;rate=24000'
        self.client=client or httpx.AsyncClient(timeout=httpx.Timeout(config.timeout),transport=httpx.AsyncHTTPTransport(retries=0),follow_redirects=False,trust_env=False)
        self.budget=budget or ElevenBudget(config,ROOT/'.tmp/tts-budgets')
    async def synthesize(self,text,style):
        text=validate_text(text);validate_style(style);self.budget.reserve(len(text))
        body={'text':text,'model_id':self.config.model,'voice_settings':{'speed':.8 if style=='slow' else 1.0}}
        try:
            async with asyncio.timeout(self.config.timeout):
                async with self.client.stream('POST','https://api.elevenlabs.io/v1/text-to-speech/'+self.config.voice,params={'output_format':'pcm_24000'},headers={'xi-api-key':self.config.api_key,'Accept':'audio/pcm'},json=body) as response:
                    if response.status_code!=200:raise TTSProviderError('tts_provider_http_error',response.status_code)
                    header=response.headers.get('content-type','').lower()
                    rate=re.search(r'rate=(\d+)',header);channels=re.search(r'channels=(\d+)',header)
                    if (rate and rate.group(1)!='24000') or (channels and channels.group(1)!='1'):raise ValueError('invalid_audio_format')
                    mime=header.split(';')[0].strip()
                    if mime not in {'audio/pcm','audio/x-pcm','audio/l16','application/octet-stream'}:raise ValueError('unsupported_tts_audio')
                    chunks=[];size=0
                    async for chunk in response.aiter_bytes(65536):
                        size+=len(chunk)
                        if size>MAX_PCM_BYTES:raise ValueError('tts_audio_size_limit')
                        chunks.append(chunk)
                    return validate_pcm(b''.join(chunks))
        except asyncio.CancelledError:raise
        except (TimeoutError,httpx.TimeoutException):raise TTSProviderError('tts_timeout') from None
        except httpx.RequestError:raise TTSProviderError('tts_transport_error') from None
    async def close(self):await self.client.aclose()

class FliteTTS:
    provider='flite';provider_api='local FFmpeg flite;pcm_s16le_24000_mono'
    source_mime='audio/L16;rate=24000'
    def __init__(self,config):config.require_ready();self.config=config;self.process=None
    async def _stop(self):
        if self.process is not None and self.process.returncode is None:
            try:self.process.terminate()
            except ProcessLookupError:pass
            try:await asyncio.wait_for(self.process.wait(),2)
            except TimeoutError:
                self.process.kill();await self.process.wait()
    async def synthesize(self,text,style):
        started=time.perf_counter();first=None
        text=validate_text(text);validate_style(style)
        folder=ROOT/'.tmp/tts-local'
        if not folder.resolve().is_relative_to(ROOT.resolve()):raise ValueError('invalid_local_tts_path')
        folder.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='flite-',dir=folder) as temporary:
            Path(temporary,'input.txt').write_text(text,encoding='utf-8')
            args=[self.config.ffmpeg,'-nostdin','-hide_banner','-loglevel','error','-f','lavfi','-i','flite=textfile=input.txt:voice='+self.config.voice]
            if style=='slow':args+=['-af','atempo=0.75']
            args+=['-t',str(MAX_TTS_SECONDS+1),'-ar','24000','-ac','1','-acodec','pcm_s16le','-f','s16le','pipe:1']
            try:
                async with asyncio.timeout(self.config.timeout):
                    self.process=await asyncio.create_subprocess_exec(*args,cwd=temporary,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
                    chunks=[];size=0
                    while chunk:=await self.process.stdout.read(65536):
                        if first is None:first=time.perf_counter()-started
                        size+=len(chunk)
                        if size>MAX_PCM_BYTES:raise ValueError('tts_audio_size_limit')
                        chunks.append(chunk)
                    if await self.process.wait()!=0:raise TTSProviderError('local_tts_process_failed')
                    pcm=validate_pcm(b''.join(chunks))
                    self.metrics={'backend_first_pcm_seconds':first,'backend_complete_seconds':time.perf_counter()-started,'native_sample_rate':8000 if self.config.voice=='kal' else None}
                    return pcm
            except TimeoutError:raise TTSProviderError('tts_timeout') from None
            finally:await self._stop()
    async def close(self):await self._stop()

def make_tts_adapter(config):
    config.require_ready()
    if config.provider=='piper':
        if config.piper_worker_mode=='resident':
            from speech_mcp.piper_resident import ResidentPiperTTS
            return ResidentPiperTTS(config)
        from speech_mcp.piper_adapter import PiperTTS
        return PiperTTS(config)
    return {'google':GoogleTTS,'elevenlabs':ElevenTTS,'flite':FliteTTS}[config.provider](config)
