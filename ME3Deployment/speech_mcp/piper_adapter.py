"""Isolated neural worker, bounded native PCM conversion and cancellation/reaping."""
import asyncio,json,os,time
from pathlib import Path
import audioop,anyio
from speech_mcp.tts_adapters import FliteTTS,TTSProviderError,validate_pcm,validate_style,MAX_PCM_BYTES,ROOT
from speech_mcp.contracts import validate_text,MAX_TTS_SECONDS
WORKER=Path(__file__).with_name('piper_worker.py')

class PCMConverter:
    def __init__(self,header):
        if not isinstance(header,dict) or set(header)!={'sample_rate','channels','sample_width'} or type(header['sample_rate']) is not int or header['sample_rate'] not in {16000,22050,24000,44100,48000} or type(header['channels']) is not int or header['channels']!=1 or type(header['sample_width']) is not int or header['sample_width']!=2:raise ValueError('invalid_piper_header')
        self.rate=header['sample_rate'];self.state=None;self.tail=b'';self.size=0;self.output_size=0
    def feed(self,chunk):
        self.size+=len(chunk)
        if self.size>self.rate*2*MAX_TTS_SECONDS:raise ValueError('tts_audio_size_limit')
        chunk=self.tail+chunk;self.tail=chunk[len(chunk)//2*2:];chunk=chunk[:len(chunk)//2*2]
        out,self.state=audioop.ratecv(chunk,2,1,self.rate,24000,self.state) if chunk else (b'',self.state)
        self.output_size+=len(out)
        if self.output_size>MAX_PCM_BYTES:raise ValueError('tts_audio_size_limit')
        return out
    def finish(self):
        if self.tail or not self.size:raise ValueError('invalid_piper_pcm')

class PiperTTS(FliteTTS):
    provider='piper';provider_api='local Piper1.8.0;native PCM16 to24000mono'
    async def synthesize(self,text,style):
        text=validate_text(text);validate_style(style);started=time.perf_counter();first=None
        # Deliberately do not forward provider credentials into the local neural worker.
        env={k:v for k,v in os.environ.items() if k in {'PATH','SYSTEMROOT','WINDIR','HOME','USERPROFILE','LD_LIBRARY_PATH'}}
        env['PYTHONUTF8']='1'
        try:
            async with asyncio.timeout(self.config.timeout):
                launch=asyncio.create_task(asyncio.create_subprocess_exec(self.config.piper_python,str(WORKER),self.config.piper_model,stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,env=env,limit=65536))
                try:self.process=await asyncio.shield(launch)
                except asyncio.CancelledError:
                    with anyio.CancelScope(shield=True):self.process=await launch
                    raise
                try:
                    self.process.stdin.write(json.dumps({'text':text,'style':style}).encode()+b'\n')
                    await self.process.stdin.drain()
                except (BrokenPipeError,ConnectionResetError):
                    # A worker can exit before reading stdin. Preserve its actual
                    # exit status instead of leaking a platform-dependent pipe race.
                    # A zero exit still has to pass the full header/PCM validation.
                    if await self.process.wait()!=0:raise TTSProviderError('local_tts_process_failed') from None
                finally:self.process.stdin.close()
                line=await self.process.stdout.readline()
                if len(line)>256:raise ValueError('invalid_piper_header')
                try:header=json.loads(line)
                except (ValueError,UnicodeError):raise ValueError('invalid_piper_header') from None
                converter=PCMConverter(header);chunks=[]
                while chunk:=await self.process.stdout.read(65536):
                    if first is None:first=time.perf_counter()-started
                    chunks.append(converter.feed(chunk))
                if await self.process.wait()!=0:raise TTSProviderError('local_tts_process_failed')
                converter.finish();pcm=validate_pcm(b''.join(chunks))
                self.metrics={'backend_first_pcm_seconds':first,'backend_complete_seconds':time.perf_counter()-started,'native_sample_rate':converter.rate}
                return pcm
        except TimeoutError:raise TTSProviderError('tts_timeout') from None
        finally:
            with anyio.CancelScope(shield=True):await self._stop()
