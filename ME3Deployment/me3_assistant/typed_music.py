"""Session-owned, lazy speaker output for typed local music. Never opens a microphone."""
import asyncio,platform,threading
from me3_assistant.music_audio import MusicBus

class Speaker:
    def __init__(self,bus,fault,external_pcm=None):
        if platform.system()!='Linux' or platform.machine() not in {'aarch64','arm64'}:raise ValueError('typed_music_requires_Pi')
        from voice_agent import Settings,configure_pi_audio
        settings=Settings.load();configure_pi_audio(settings)
        if (settings.output_rate,settings.output_channels)!=(48000,2):raise ValueError('music_requires_existing_48k_stereo_output')
        import pyaudio
        self.audio=pyaudio.PyAudio();self.stream=None
        def callback(data,frames,timing,status):
            silence=bytes(frames*4)
            if status:fault.set();return silence,pyaudio.paAbort
            try:
                mixed=bus.mix(silence)
                return (external_pcm.mix(mixed) if external_pcm is not None else mixed),pyaudio.paContinue
            except Exception:fault.set();return silence,pyaudio.paAbort
        try:self.stream=self.audio.open(format=pyaudio.paInt16,channels=2,rate=48000,output=True,input=False,output_device_index=settings.output_device,frames_per_buffer=2880,stream_callback=callback)
        except BaseException:self.close();raise
    def close(self):
        try:
            if self.stream:
                try:self.stream.stop_stream()
                finally:self.stream.close();self.stream=None
        finally:
            if self.audio:self.audio.terminate();self.audio=None

class TypedMusicOutput:
    def __init__(self,path,events,*,opener=Speaker,external_pcm=None):
        self.bus=MusicBus(path,create=True);self.events=events;self.opener=opener;self.external_pcm=external_pcm;self.fault=threading.Event()
        self.open_task=None;self.resource=None;self.closing=False;self.closed=False;self.failed=False
    async def _open(self):
        try:
            self.resource=await asyncio.to_thread(self.opener,self.bus,self.fault,**({'external_pcm':self.external_pcm} if self.external_pcm is not None else {}))
            if self.closing:raise ValueError('typed_music_closed')
            async with asyncio.timeout(2):
                while not self.bus.snapshot()['output_attached']:
                    if self.fault.is_set():raise ValueError('typed_music_output_failed')
                    await asyncio.sleep(.02)
            self.events.emit('assistant_typed_music_output_opened',microphone_opened=False)
        except BaseException:
            self.failed=True;self.fault.set()
            if self.resource:
                resource=self.resource;self.resource=None;await asyncio.to_thread(resource.close)
            raise
    async def ensure_open(self):
        if self.closing or self.closed or self.failed or self.fault.is_set():raise ValueError('typed_music_output_unavailable')
        if self.open_task is None:self.open_task=asyncio.create_task(self._open())
        # A canceled request cannot orphan a late-opening stream. Its session
        # retains the open task and closes it after the MCP decoder has exited.
        await asyncio.shield(self.open_task)
        if self.closing or self.fault.is_set():raise ValueError('typed_music_output_unavailable')
    async def aclose(self):
        if self.closed:return
        self.closing=True
        if self.open_task:await asyncio.gather(self.open_task,return_exceptions=True)
        try:
            if self.resource:
                resource=self.resource;self.resource=None;await asyncio.to_thread(resource.close)
        finally:self.bus.close(destroy=True);self.closed=True
