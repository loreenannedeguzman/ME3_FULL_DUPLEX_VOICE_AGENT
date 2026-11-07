"""Synthetic fixture support only; no discovered tests or baseline-bug assertions.

The candidate test installs the actual staged lifecycle methods and overlap hook.
No provider, audio device, inference, socket or canonical writes.
"""
import ast,asyncio,base64,collections,copy,enum,math,os,re,sys,time,types,unittest
from pathlib import Path
ROOT=Path(os.environ.get('ME3_PROJECT_ROOT','/opt/ME3Deployment'))

def selected(path,names,namespace):
    tree=ast.parse(path.read_text(encoding='utf-8'))
    nodes=[n for n in tree.body if isinstance(n,(ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in names]
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(path),'exec'),namespace)
    return namespace

# Reuse the established no-I/O Windows runner; no device/controller imports.
LOOP=selected(ROOT/'tests/test_speech_phrasing243.py',{'_NoIOSelector','_NoIOLoop','run_audio_fixture'},dict(asyncio=asyncio,time=time,sys=sys))
VAD=enum.Enum('VAD','QUIET STARTING SPEAKING STOPPING')
class Frame:
    def __init__(self,**kw):self.__dict__.update(kw)
class Input(Frame):pass
class Start(Frame):pass
class Cancel(Frame):pass
class End(Frame):pass
class Base:
    async def process_frame(self,*a):pass
class Events:
    def __init__(self):self.rows=[];self.input_frames=0
    def emit(self,event,**kw):self.rows.append(dict(event=event,**kw))

class Fixture:
    async def open(self,pending=None):
        self.events=Events();policy=dict(re=re,math=math)
        tree=ast.parse((ROOT/'me3_completion/policy.py').read_text())
        exec(compile(ast.Module(body=[n for n in tree.body if not isinstance(n,(ast.Import,ast.ImportFrom))],type_ignores=[]),'policy.py','exec'),policy)
        source=ast.parse((ROOT/'me3_completion/pipeline.py').read_text())
        cls=next(n for n in ast.walk(source) if isinstance(n,ast.ClassDef) and n.name=='ConversationProcessor')
        methods=[copy.deepcopy(n) for n in cls.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in {'valid','process_frame'}]
        node=ast.ClassDef(name='SourceProcessor',bases=[ast.Name(id='FrameProcessor',ctx=ast.Load())],keywords=[],body=methods,decorator_list=[])
        ns=dict(FrameProcessor=Base,FrameDirection=types.SimpleNamespace(UPSTREAM='up'),InputAudioRawFrame=Input,
                StartFrame=Start,CancelFrame=Cancel,EndFrame=End,VADState=VAD,events=self.events,time=time)
        exec(compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),'actual_pipeline_bodies','exec'),ns)
        p=self.p=ns['SourceProcessor']();p.closing=False;p.token=7;p.speaking=False;p.last_vad=VAD.QUIET;p.probing=True
        p.pcm=bytearray(b'\x01\x00'*8000);p.preroll=collections.deque(maxlen=50);p.overflow=False;p.cursor=0;p.starting=None;p.onset=0;p.active_end=0
        p.max_pcm_bytes=480000;p.decision=None;p.probe=None;p.cue=None;p.cued=False;p.jobs=[]
        playback=selected(ROOT/'me3_completion/playback.py',{'PlaybackGate'},dict(asyncio=asyncio))
        class Buffer:
            def __init__(self):self.paused=False;self.clears=0
            def suspend(self):self.paused=True
            def resume(self):self.paused=False
            def clear(self):self.clears+=1
        p.buffer=Buffer();p.gate=playback['PlaybackGate'](p.buffer);p.gate.suspend()
        self.clock=types.SimpleNamespace(current=1);self.adopted=[];self.reserves=0
        def reserve():self.reserves+=1;return True
        p.controller=types.SimpleNamespace(clock=self.clock,current=lambda g:g==self.clock.current,turns=0,max_turns=8,
             reserve_overlap_probe=reserve,credit_overlap=lambda g:None,listener_ack_count=0,submit_recognized=lambda *a:self.adopted.append(a))
        self.keep=asyncio.Event()
        async def old():await self.keep.wait()
        p.output_task=asyncio.create_task(old());p.output_kind='response';p.output_generation=1
        self.old=p.output_task;self.provider=types.SimpleNamespace(pending=pending)
        p.state=types.SimpleNamespace(capture_count=0);p.transition=lambda *a:None
        ns.update(state=p.state,gate=p.gate,Ownership=types.SimpleNamespace(USER='user',SUSPENDED='suspended'))
        async def push(*a):pass
        p.push_frame=push
        async def takeover(reason):
            self.clock.current+=1;p.probing=False;p.gate.resume();p.buffer.clear();self.old.cancel();return self.clock.current
        p.takeover=takeover
        def spawn(coro):
            task=asyncio.create_task(coro);p.jobs.append(task);return task
        p.spawn=spawn
        self.activity=VAD.QUIET
        async def analyze(data):return self.activity
        p.vad=types.SimpleNamespace(analyze_audio=analyze)
        self.entered=asyncio.Event();self.release=asyncio.Event();self.calls=[]
        async def call(role,g,**args):
            self.calls.append((role,g,args));self.entered.set();await self.release.wait()
            return dict(text='okay',generation=g,request_id='asr-fixture-exact')
        clients=types.SimpleNamespace(call=call)
        return self
    async def start_probe(self):
        self.p.probe=asyncio.create_task(self.p.classify_overlap(7,bytes(self.p.pcm),.38,1,self.old))
        await self.entered.wait();return self.p.probe
    async def feed(self,state):
        self.activity=state
        await self.p.process_frame(Input(audio=b'\x00\x00'*320,sample_rate=16000,num_channels=1),'down')
        await asyncio.sleep(0)
    async def close(self):
        jobs=[self.old,self.p.probe,*self.p.jobs]
        for t in jobs:
            if t is not None:t.cancel()
        await asyncio.gather(*(t for t in jobs if t is not None),return_exceptions=True)

