"""One optional cached presentation cue; no planner, effects or synthesis on turns."""
import asyncio,uuid
from contextlib import asynccontextmanager
from speech_mcp.contracts import decode_pcm,MAX_TTS_SECONDS

TEXT='Sure.'
DELAY_SECONDS=.25
MAX_PCM_BYTES=72000 #1.5seconds,24kHz monoPCM16; never truncate a longer clip.
SETTLEMENT_SECONDS=.5

class PlanningAck:
    def __init__(self,processor,events):
        self.processor=processor;self.controller=processor.controller;self.provider=self.controller.provider;self.events=events
        self.pcm=None;self.identity=None;self.record=None;self.prepare_attempted=False
    def same_owner(self,r):
        c=self.controller;p=self.processor
        return (not p.closing and not p.pipeline_failed.is_set() and not c.closed
            and c.current(r['generation']) and c.task is r['owner'] and not r['owner'].done()
            and self.provider.active_generation==r['generation'] and self.provider.context.goal_id==r['goal'])
    def valid(self,token,generation):
        r=self.record;p=self.processor
        return (r is not None and r['active'] and r['token_live'] and r['token']==token
            and r['generation']==generation and self.same_owner(r) and not self.provider.pending
            and not p.speaking and getattr(getattr(p,'last_vad',None),'name',None) not in {'STARTING','SPEAKING'})
    def english_context(self):
        # Language comes only from already validated submitted reply segments,
        # never a guess about the user's words or the generated-but-unplayed answer.
        entries=getattr(getattr(self.provider.context,'retained',None),'entries',[])
        for entry in reversed(entries):
            offset=entry.get('offset',0)
            if not offset:continue
            rows=entry.get('speech_segments')
            if rows is None:return True # Existing plain host speech is English.
            at=0
            for row in rows:
                at+=len(row['text'])
                if offset<=at:return row['language']=='en'
            return False
        return True
    def reset_valid(self,token,generation):
        r=self.record
        return r is not None and r['retiring'] and r['token']==token and r['generation']==generation and self.same_owner(r)
    async def prepare(self):
        """One counted real synthesis after greeting, before user capture is armed."""
        c=self.controller;p=self.processor;clients=c.real_clients
        if self.prepare_attempted or p.closing or c.closed or c.clock.current!=0 or getattr(p,'candidate_listening',False):return
        self.prepare_attempted=True
        identity=getattr(clients,'tts_identity',None)
        if not isinstance(identity,tuple) or len(identity)!=2 or identity[0]!='piper':return
        # Leave at least one request for a real answer. This does not reserve or
        # refund anything: the ordinary Speech.call owns the actual TTS charge.
        if self.provider.engine.budget.remaining('tts')<2:return
        data=await clients.call('tts',0,text=TEXT,style='normal',language='en')
        if p.closing or c.closed or c.clock.current!=0:return
        if (data.get('provider'),data.get('model'))!=identity or data.get('language')!='en':raise ValueError('planning_ack_voice_identity_mismatch')
        if data.get('sample_rate')!=24000 or data.get('channels')!=1 or data.get('encoding')!='pcm_s16le':raise ValueError('planning_ack_PCM_format_mismatch')
        pcm=decode_pcm(data['pcm_b64'],24000,MAX_TTS_SECONDS)
        if len(pcm)>MAX_PCM_BYTES:
            self.events.emit('planning_ack_cache_unavailable',reason='clip_not_brief',syntheses=1);return
        self.pcm=bytes(pcm);self.identity=identity
        self.events.emit('planning_ack_cache_ready',language='en',byte_count=len(pcm),syntheses=1,audible=False)
    def output_owned(self,token,generation):
        if not self.valid(token,generation):return False
        self.record['output_owned']=True
        return True
    def user_onset(self):
        """Retire optional audio on provisional VAD, without changing main ownership."""
        r=self.record
        if r is None or not r['token_live'] or not self.same_owner(r):return
        r['token_live']=False
        job=r.get('job')
        if job is not None and not job.done() and not job.cancelling():job.cancel()
        if r['output_owned'] and self.processor.output_kind in {None,'planning_ack'}:
            epoch,discarded=self.processor.state.buffer.clear()
            self.events.emit('planning_ack_retired_on_onset',generation=r['generation'],epoch=epoch,discarded_bytes=discarded,ordinary_goal_cancelled=False)
    async def _delayed(self,r):
        await asyncio.sleep(DELAY_SECONDS)
        if not self.valid(r['token'],r['generation']):return
        if self.pcm is None or not self.english_context() or self.identity!=getattr(self.controller.real_clients,'tts_identity',None):return
        self.events.emit('planning_ack_playout_requested',generation=r['generation'],kind='planning_ack',cached=True,TTS_requests=0,reasoning_requests=0,substantive_answer=False)
        await self.processor.play(self.pcm,r['generation'],'planning_ack',planning_ack_token=r['token'])
    async def retire(self,r):
        r['active']=False;r['token_live']=False;r['retiring']=True
        job=r.get('job')
        if job is not None:
            if not job.done() and not job.cancelling():job.cancel()
            # No speech RPC is involved. A stuck local producer must never be
            # abandoned under a new answer or silently clear another owner.
            interrupted=False;deadline=asyncio.get_running_loop().time()+SETTLEMENT_SECONDS
            while not job.done():
                remaining=deadline-asyncio.get_running_loop().time()
                if remaining<=0:self.processor.pipeline_failed.set();raise RuntimeError('planning_ack_playout_unsettled')
                try:await asyncio.wait({job},timeout=remaining)
                except asyncio.CancelledError:interrupted=True
            if not job.cancelled():job.result()
        else:interrupted=False
        if r['output_owned'] and self.same_owner(r) and r.get('reset') is None:
            # Conversation has not returned its answer yet. No ordinary speech
            # may be cleared here; a new generation's cleanup is never ours.
            if self.processor.output_kind not in {None,'planning_ack'}:
                self.processor.pipeline_failed.set();raise RuntimeError('planning_ack_output_owner_changed')
            from pipecat.frames.frames import InterruptionFrame
            frame=InterruptionFrame();frame.m3_generation=r['generation'];frame.m3_planning_ack_reset=r['token']
            async def clear():
                await self.processor.push_frame(frame)
                await r['reset_applied'].wait()
                if r['reset_error'] is not None:raise RuntimeError('planning_ack_reset_failed') from r['reset_error']
            r['reset']=asyncio.create_task(clear())
        reset=r.get('reset')
        if reset is not None:
            deadline=asyncio.get_running_loop().time()+SETTLEMENT_SECONDS
            while not reset.done():
                if not self.same_owner(r) and not reset.cancelling():reset.cancel()
                remaining=deadline-asyncio.get_running_loop().time()
                if remaining<=0:self.processor.pipeline_failed.set();raise RuntimeError('planning_ack_reset_unsettled')
                try:await asyncio.wait({reset},timeout=min(.01,remaining))
                except asyncio.CancelledError:interrupted=True
            if not reset.cancelled():reset.result()
            if self.same_owner(r):
                self.processor.gate.resume()
                self.events.emit('planning_ack_output_retired',generation=r['generation'],epoch=self.processor.state.buffer.epoch,substantive_answer_started=False)
        if self.record is r:self.record=None
        if interrupted:raise asyncio.CancelledError()
    @asynccontextmanager
    async def scope(self,generation,goal):
        if self.pcm is None or self.provider.pending or not self.english_context():
            yield;return
        if self.record is not None:raise RuntimeError('planning_ack_scope_already_owned')
        r=dict(generation=generation,goal=goal,owner=asyncio.current_task(),token=uuid.uuid4().hex,token_live=True,active=True,output_owned=False,job=None,retiring=False,reset=None,reset_applied=asyncio.Event(),reset_error=None)
        if not self.same_owner(r):
            yield;return
        self.record=r;r['job']=asyncio.create_task(self._delayed(r))
        try:yield
        finally:await self.retire(r)
    async def close(self):
        if self.record is not None:await self.retire(self.record)
        self.pcm=None;self.identity=None

def install(processor,events):
    """Normal composition only; shared/legacy startup remains unchanged."""
    manager=PlanningAck(processor,events);processor.planning_ack=manager
    processor.prepare_planning_ack=manager.prepare;processor.controller.provider.planning_ack_scope=manager.scope
    output=processor.output_transport;original=output.write_audio_frame
    original_process=output.process_frame
    async def process(frame,direction):
        g=getattr(frame,'m3_generation',None);reset=getattr(frame,'m3_planning_ack_reset',None);token=getattr(frame,'m3_planning_ack_token',None)
        if reset is not None and not manager.reset_valid(reset,g):return
        if token is not None and not manager.valid(token,g):return
        if reset is not None:
            record=manager.record
            try:return await original_process(frame,direction)
            except BaseException as error:
                record['reset_error']=error;raise
            finally:record['reset_applied'].set()
        return await original_process(frame,direction)
    async def write(frame):
        token=getattr(frame,'m3_planning_ack_token',None)
        if token is not None and not manager.valid(token,getattr(frame,'m3_generation',None)):return False
        # The original writer captures buffer.epoch before its first await;
        # a subsequent clear rejects that writer's old-epoch backpressure retry.
        return await original(frame)
    output.write_audio_frame=write
    output.process_frame=process
    return manager
