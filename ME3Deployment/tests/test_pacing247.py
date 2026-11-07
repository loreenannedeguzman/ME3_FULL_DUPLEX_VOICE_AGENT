"""Actual producer body + actual bounded callback buffer, virtual clock only."""
import ast, asyncio, copy, hashlib, json, os, runpy, types, unittest
from pathlib import Path
HERE=Path(__file__).resolve().parent
ROOT=Path(os.environ.get('ME3_PROJECT_ROOT',Path(__file__).resolve().parents[1]))
STAGE=Path(os.environ.get('ME3_LATENCY_STAGE',str(ROOT)))
Buffer=runpy.run_path(str(ROOT/'duplex_audio.py'))['PlayoutBuffer']
OLD='legacy';NEW=STAGE/'me3_completion/pipeline.py'
LEGACY_PLAY="async def play(self, pcm, generation, kind='response'):\n    async with self.output_lock:\n        self.output_generation = generation\n        self.output_kind = kind\n        self.output_task = asyncio.current_task()\n        self.transition(Ownership.BACKCHANNEL if kind == 'backchannel' else Ownership.RESPONSE, 'output_started')\n\n        async def emit(frame):\n            frame.m3_generation = generation\n            await self.push_frame(frame)\n        try:\n            await emit(LLMFullResponseStartFrame())\n            await emit(TTSStartedFrame())\n            first = True\n            for offset in range(0, len(pcm), 960):\n                await gate.wait()\n                if not self.controller.current(generation):\n                    return\n                await emit(OutputAudioRawFrame(audio=pcm[offset:offset + 960], sample_rate=24000, num_channels=1))\n                events.output_frames += 1\n                if first:\n                    events.emit('generation_playout_started', generation=generation, kind=kind, capture_count=state.capture_count)\n                    first = False\n                await asyncio.sleep(0.02)\n            if self.controller.current(generation):\n                await emit(TTSStoppedFrame())\n                await emit(LLMFullResponseEndFrame())\n                events.emit('generation_playout_submitted', generation=generation, kind=kind, capture_count=state.capture_count)\n        finally:\n            self.output_generation = None\n            self.output_kind = None\n            self.output_task = None\n            if self.ownership in {Ownership.RESPONSE, Ownership.BACKCHANNEL}:\n                self.transition(Ownership.HOLD if self.pcm else Ownership.LISTENING, 'output_settled')"
class Lock:
    async def __aenter__(self):pass
    async def __aexit__(self,*a):pass
class Event:
    def __init__(self):self.value=True
    def is_set(self):return self.value
class Frame:
    def __init__(self,**kw):self.__dict__.update(kw)
class Events:
    def __init__(self):self.rows=[];self.output_frames=0
    def emit(self,event,**kw):self.rows.append((event,kw))
class Harness:
    def __init__(self,path,cost=.0015,pause_at=None,cancel_at=None,stall_at=None,callback_phase=.06):
        self.t=0.;self.next_callback=callback_phase;self.buffer=Buffer(23040)
        self.callbacks=[];self.offered=[];self.max_queue=0;self.alive=True
        self.cost=cost;self.pause_at=pause_at;self.cancel_at=cancel_at;self.stall_at=stall_at
        self.ready=Event();self.events=Events();self.capture_count=0;self.pauses=0
        self.output_lock=Lock();self.ownership='listening';self.pcm=b''
        self.controller=types.SimpleNamespace(current=lambda g:self.alive)
        self.gate=types.SimpleNamespace(ready=self.ready,wait=self.wait)
        self.sleeps=[];self.emission_times=[]
        fake=types.SimpleNamespace(get_running_loop=lambda:self,sleep=self.sleep,current_task=lambda:object())
        ns=dict(asyncio=fake,gate=self.gate,events=self.events,state=self,
          Ownership=types.SimpleNamespace(BACKCHANNEL='back',RESPONSE='response',HOLD='hold',LISTENING='listening'),
          LLMFullResponseStartFrame=Frame,TTSStartedFrame=Frame,OutputAudioRawFrame=Frame,
          TTSStoppedFrame=Frame,LLMFullResponseEndFrame=Frame)
        tree=ast.parse(LEGACY_PLAY if path=='legacy' else path.read_text());fn=next(n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) and n.name=='play')
        exec(compile(ast.fix_missing_locations(ast.Module(body=[copy.deepcopy(fn)],type_ignores=[])),str(path),'exec'),ns)
        self.play=types.MethodType(ns['play'],self)
    def time(self):return self.t
    def advance(self,seconds):
        end=self.t+seconds
        while self.next_callback<=end+1e-9:
            self.t=self.next_callback
            data,epoch,played,queued=self.buffer.take(11520)
            self.callbacks.append(dict(at=round(self.t,9),played=played,data=data[:played],epoch=epoch))
            self.next_callback+=.06
        self.t=end
    async def sleep(self,seconds):
        self.sleeps.append(seconds);self.advance(seconds)
        if self.pause_at==len(self.offered) and not self.pauses:self.ready.value=False
    async def wait(self):
        if not self.ready.value:
            self.advance(4.);self.ready.value=True;self.pauses+=1
        if self.cancel_at==('gate',len(self.offered)):
            self.alive=False;self.buffer.clear();raise asyncio.CancelledError()
    def transition(self,value,reason):self.ownership=value
    async def push_frame(self,frame):
        if not hasattr(frame,'audio'):return
        index=len(self.offered)
        if self.stall_at==index:self.advance(.25)
        self.advance(self.cost)
        if self.cancel_at==('push',index):
            self.alive=False;self.buffer.clear();raise asyncio.CancelledError()
        self.offered.append(frame.audio);self.emission_times.append(self.t)
        # Synthetic exact24k-mono->48k-stereo expansion; no resampler claim.
        expanded=b''.join(frame.audio[i:i+2]*4 for i in range(0,len(frame.audio),2))
        result=self.buffer.offer(expanded,self.buffer.epoch)
        if result!='accepted':raise AssertionError('bounded_buffer_overflow')
        self.max_queue=max(self.max_queue,self.buffer._size)
    def run(self,pcm):
        c=self.play(pcm,1)
        try:c.send(None)
        except StopIteration:pass
        else:raise AssertionError('unexpected_real_async_io')
    def settle(self):self.advance(.18)
    def partial_inside(self):return sum(0<r['played']<11520 for r in self.callbacks[:-1])
    def metric(self):return dict(seconds=round(self.t,6),frames=len(self.offered),partial_callbacks=self.partial_inside(),
       inserted_zero_ms=round(sum(11520-r['played'] for r in self.callbacks[:-1])/192,3),max_queue_bytes=self.max_queue)

PCM=b'\x65\x01'*240000
class Pacing(unittest.TestCase):
    def test_actual_producer_overhead_no_longer_accumulates_as_callback_zeros(self):
        old=Harness(OLD);new=Harness(NEW);old.run(PCM);new.run(PCM)
        self.assertGreater(old.partial_inside(),20);self.assertEqual(new.partial_inside(),0)
        self.assertGreater(old.t,new.t+.5);self.assertAlmostEqual(new.t,10.,places=7)
        self.assertEqual(b''.join(old.offered),PCM);self.assertEqual(b''.join(new.offered),PCM)
        self.assertLessEqual(new.max_queue,23040)
        print('comparison',json.dumps(dict(old=old.metric(),new=new.metric()),sort_keys=True))
    def test_actual_callback_preserves_all_pcm_order_and_partial_last_frame_duration(self):
        pcm=PCM[:4800]+b'\x66\x02'*137
        h=Harness(NEW);h.run(pcm);self.assertAlmostEqual(h.t,len(pcm)/48000,places=7);h.settle()
        expanded=b''.join(pcm[i:i+2]*4 for i in range(0,len(pcm),2))
        self.assertEqual(b''.join(r['data'] for r in h.callbacks),expanded)
        self.assertEqual(b''.join(h.offered),pcm)
    def test_four_second_ack_hold_rebases_without_catchup_burst_or_lost_samples(self):
        h=Harness(NEW,pause_at=20);h.run(PCM[:48000]);h.settle()
        self.assertEqual(h.pauses,1);self.assertEqual(b''.join(h.offered),PCM[:48000])
        self.assertGreater(h.emission_times[20]-h.emission_times[19],4.)
        self.assertGreaterEqual(min(b-a for a,b in zip(h.emission_times[20:],h.emission_times[21:])),.019999)
        self.assertLessEqual(h.max_queue,23040)
    def test_late_push_catches_up_no_more_than_one_frame(self):
        h=Harness(NEW,stall_at=20);h.run(PCM[:48000]);self.assertLessEqual(h.max_queue,23040)
        gaps=[b-a for a,b in zip(h.emission_times,h.emission_times[1:])]
        self.assertLessEqual(sum(g<.018 for g in gaps),1)
        self.assertEqual(b''.join(h.offered),PCM[:48000])
    def test_callback_start_phase_never_causes_recurrent_underfill(self):
        for phase in [.005,.02,.04,.06]:
            with self.subTest(phase=phase):
                h=Harness(NEW,callback_phase=phase);h.run(PCM[:48000])
                self.assertTrue(all(r['played']==11520 for r in h.callbacks[1:]))
                self.assertLessEqual(h.max_queue,23040)
    def test_cancel_during_wait_never_submits_retained_audio(self):
        h=Harness(NEW,cancel_at=('gate',20))
        with self.assertRaises(asyncio.CancelledError):h.run(PCM)
        self.assertEqual(len(h.offered),20);self.assertEqual(h.buffer._size,0)
        h.settle();self.assertTrue(all(r['played']==0 for r in h.callbacks[-3:]))
        self.assertIsNone(h.output_task);self.assertIsNone(h.output_generation)
    def test_cancel_during_push_clears_epoch_and_has_no_late_output(self):
        h=Harness(NEW,cancel_at=('push',20))
        with self.assertRaises(asyncio.CancelledError):h.run(PCM)
        self.assertEqual(len(h.offered),20);self.assertEqual(h.buffer.epoch,1);h.settle()
        self.assertTrue(all(r['played']==0 for r in h.callbacks[-3:]))
        self.assertFalse(any(e=='generation_playout_submitted' for e,_ in h.events.rows))
    def test_generation_invalidated_during_blocked_push_is_fenced_without_task_cancel(self):
        h=Harness(NEW);original_push=h.push_frame;seen=[]
        class BlockedFrameProcessor:
            async def process_frame(self,frame,direction):
                if hasattr(frame,'audio'):
                    seen.append(frame)
                    if len(seen)==21:
                        # The task survives the await, but its generation does not.
                        h.advance(.25);h.alive=False;h.buffer.clear()
        tree=ast.parse(NEW.read_text())
        node=next(n for n in ast.walk(tree) if isinstance(n,ast.ClassDef) and n.name=='GenerationFilter')
        ns=dict(FrameProcessor=BlockedFrameProcessor,processor=h)
        exec(compile(ast.fix_missing_locations(ast.Module(body=[copy.deepcopy(node)],type_ignores=[])),str(NEW),'exec'),ns)
        guard=ns['GenerationFilter']()
        async def downstream(frame,direction):await original_push(frame)
        guard.push_frame=downstream
        async def through_guard(frame):await guard.process_frame(frame,None)
        h.push_frame=through_guard;h.run(PCM)
        self.assertEqual(len(seen),21);self.assertEqual(len(h.offered),20)
        self.assertEqual(h.buffer.epoch,1);self.assertEqual(h.buffer._size,0)
        self.assertFalse(any(e=='generation_playout_submitted' for e,_ in h.events.rows))
        self.assertIsNone(h.output_task);h.settle()
        self.assertTrue(all(r['played']==0 for r in h.callbacks[-3:]))
        # An old queued frame cannot reopen output, including after a fresh owner.
        async def late_and_fresh():
            h.alive=True;h.controller.current=lambda g:h.alive and g==2
            await guard.process_frame(Frame(audio=b'\x33\x01'*480,m3_generation=1),None)
            self.assertEqual(h.buffer._size,0);self.assertEqual(len(h.offered),20)
            fresh=b'\x77\x02'*480
            await guard.process_frame(Frame(audio=fresh,m3_generation=2),None)
            self.assertEqual(h.offered[-1],fresh);self.assertEqual(len(h.offered),21)
            data,epoch,played,queued=h.buffer.take(11520)
            self.assertEqual((epoch,played,queued),(1,3840,0))
            self.assertEqual(data[:played],b'\x77\x02'*1920)
        c=late_and_fresh()
        try:c.send(None)
        except StopIteration:pass
        else:self.fail('unexpected_real_async_io')
    def test_only_play_body_changed_no_text_projection_reference_or_policy_edits(self):
        def rest(path):
            tree=ast.parse(path.read_text())
            for n in ast.walk(tree):
                if isinstance(n,ast.AsyncFunctionDef) and n.name=='play':n.body=[ast.Pass()]
            return ast.dump(tree,include_attributes=False)
        self.assertEqual(hashlib.sha256(rest(NEW).encode()).hexdigest(),'261d7944a11863b0a09ca2881ce8a5d7cec74ae223c2bf5c477a194bdf0f6001')
        self.assertEqual(hashlib.sha256(LEGACY_PLAY.encode()).hexdigest(),'0e6c8565d724e9d4c011b657d22d86e9779dc55144e60b5e363db102b4dafadd')
if __name__=='__main__':unittest.main(verbosity=2)
