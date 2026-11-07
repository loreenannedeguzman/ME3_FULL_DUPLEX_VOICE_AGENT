"""DEFAULT-OFF speech-owner prototype; no production imports or authority.

The injected admission callback must verify a host-issued immutable commit.
Parsing a model unit is not admission. This module does not create a native
seal, authorize tools, or establish audible completion. Source/projected text
are kept separate; only post-software-playout projected prefixes are submitted.
"""
import asyncio
from dataclasses import dataclass

class DeliveryError(ValueError):pass

@dataclass(frozen=True)
class Binding:
    goal_id:str
    engine_generation:int
    owner_generation:int
    stream_id:str

@dataclass(frozen=True)
class Unit:
    binding:Binding
    index:int
    language:str
    text:str
    projection:str='source'

@dataclass(frozen=True)
class FullComplete:
    binding:Binding
    units:int
    source_text:str
    projected_text:str
    phase:str='FULL_COMPLETE'

@dataclass(frozen=True)
class Chunk:
    text:str
    language:str
    unit_index:int

@dataclass(frozen=True)
class Outcome:
    status:str
    submitted_prefix:str
    source_text:str
    terminal_verified:bool
    cleanup_verified:bool
    acoustic_completion_measured:bool=False

_END=object()

class QueuedDelivery:
    """One bounded delivery owner, with explicit producer and output failure.

    synthesize/play/clear_output are async injected existing-owner operations.
    project(text,language) and split(projected) are host projections. submit
    receives the exact cumulative projected prefix, never markup/rephrased text.
    finalize may run only after FULL_COMPLETE and complete software playout.
    A production adapter must connect submit to partial shared history and, once
    the final result exists, register/reconcile the existing retained entry.
    """
    DEFAULT_ENABLED=False
    MAX_CHARS=40000
    MAX_CHUNKS=512
    def __init__(self,binding,*,enabled=False,current,admit,project,split,
                 synthesize,play,clear_output,submit,finalize):
        if type(enabled) is not bool:raise DeliveryError('boolean_feature_flag_required')
        if not isinstance(binding,Binding):raise DeliveryError('host_binding_required')
        self.binding=binding;self.enabled=enabled;self.current=current;self.admit=admit
        self.project=project;self.split=split;self.synthesize=synthesize;self.play=play
        self.clear_output=clear_output;self.submit=submit;self.finalize=finalize
        self.input=asyncio.Queue(maxsize=self.MAX_CHUNKS+1);self.audio=asyncio.Queue(maxsize=2)
        self.units=[];self.projected=[];self.chunks=0;self.prefix='';self.terminal=None
        self.state='open';self.workers=[];self.run_started=False;self.cleanup_verified=False
        self._clear_started=False;self._abort_started=False;self.failure=None;self._abort_lock=asyncio.Lock()
        self.owner_task=None;self.play_settled=asyncio.Event();self.play_settled.set()
        self.control=None;self.control_started=False;self.control_playing=False;self.control_receipt=None;self.control_used=False;self.control_valid=None;self.control_synthesize=None;self.control_play=None
    async def offer_control(self,receipt,*,valid,synthesize,play):
        self.require_enabled();self.require_owner()
        if self.control is not None or self.control_used:raise DeliveryError('single_task_control_required')
        if self.units or self.terminal is not None:return False
        if not callable(valid) or not valid(receipt):raise DeliveryError('task_control_not_host_issued')
        self.control=receipt;self.control_valid=valid;self.control_synthesize=synthesize;self.control_play=play
        return True
    async def deliver_control(self):
        # Inline original owner, BEFORE any answer synthesis worker starts.
        # Control never enters units, prefixes, history or terminal validation.
        if self.workers or self.run_started:raise DeliveryError('task_control_after_answer_worker')
        receipt=self.control
        if receipt is None:return False
        self.control=None;self.control_used=True;self.owner_task=asyncio.current_task()
        if not self.owned() or self.units or self.terminal is not None or not self.control_valid(receipt):return False
        self.control_started=True;self.play_settled.clear()
        try:
            pcm=await self.control_synthesize(receipt.text)
            if not self.owned() or self.units or self.terminal is not None or not self.control_valid(receipt):return False
            self.control_receipt=receipt;self.control_playing=True
            await self.control_play(pcm)
            return True
        finally:
            self.control_playing=False;self.control_receipt=None;self.play_settled.set()
    def require_enabled(self):
        if not self.enabled:raise DeliveryError('validated_delivery_disabled')
    def owned(self):return bool(self.current(self.binding))
    def require_owner(self):
        if self.state!='open' or not self.owned():raise DeliveryError('delivery_owner_invalidated')
    async def offer(self,unit):
        self.require_enabled()
        try:
            self.require_owner()
            if self.terminal is not None:raise DeliveryError('unit_after_terminal')
            if not isinstance(unit,Unit) or unit.binding!=self.binding or type(unit.index) is not int or unit.index!=len(self.units):raise DeliveryError('unit_binding_or_sequence_invalid')
            if unit.language not in {'en','es'} or unit.projection!='source' or not isinstance(unit.text,str) or not unit.text.strip() or '\x00' in unit.text:raise DeliveryError('invalid_unit_presentation')
            if not self.admit(unit,'offer'):raise DeliveryError('host_unit_not_admitted')
            source=''.join(row.text for row in self.units)+unit.text
            if len(source)>self.MAX_CHARS:raise DeliveryError('source_bound')
            projected=self.project(unit.text,unit.language)
            if not isinstance(projected,str) or not projected.strip() or '\x00' in projected:raise DeliveryError('invalid_projection')
            parts=self.split(projected)
            if not isinstance(parts,(list,tuple)) or not parts or any(not isinstance(p,str) or not p for p in parts) or ''.join(parts)!=projected:raise DeliveryError('lossless_chunks_required')
            if len(''.join(self.projected))+len(projected)>self.MAX_CHARS or self.chunks+len(parts)>self.MAX_CHUNKS:raise DeliveryError('projected_delivery_bound')
            # Admission is atomic; never partially enqueue one rejected unit.
            if self.input.qsize()+len(parts)>self.MAX_CHUNKS:raise DeliveryError('bounded_queue_capacity')
            self.units.append(unit);self.projected.append(projected);self.chunks+=len(parts)
            for part in parts:self.input.put_nowait(Chunk(part,unit.language,unit.index))
        except BaseException:
            await self.abort('unit_validation_failed');raise
    async def finish(self,terminal):
        self.require_enabled()
        try:
            self.require_owner()
            if self.terminal is not None or not isinstance(terminal,FullComplete) or terminal.binding!=self.binding or terminal.phase!='FULL_COMPLETE':raise DeliveryError('invalid_terminal_binding')
            if type(terminal.units) is not int or terminal.units!=len(self.units) or not self.units:raise DeliveryError('terminal_unit_count_mismatch')
            if terminal.source_text!=''.join(u.text for u in self.units) or terminal.projected_text!=''.join(self.projected):raise DeliveryError('terminal_prefix_mismatch')
            if not self.admit(terminal,'full_complete'):raise DeliveryError('native_terminal_not_admitted')
            self.terminal=terminal
            # Sentinel needs one independent slot even when every unit has
            # arrived before run. Backpressure is bounded by the active owner.
            await self.input.put(_END)
        except BaseException:
            await self.abort('terminal_validation_failed');raise
    def _drain(self,queue):
        while not queue.empty():queue.get_nowait()
    async def abort(self,reason='owner_cancelled'):
        if not self.enabled:return
        async with self._abort_lock:
            if self.state=='complete':return
            self.failure=reason
            if self.state=='open':self.state='cancelled' if reason=='owner_cancelled' else 'partial_failed' if self.prefix else 'failed'
            current=asyncio.current_task()
            first_abort=not self._abort_started;self._abort_started=True
            if first_abort and self.owner_task is not None and self.owner_task is not current and not self.owner_task.done():
                self.owner_task.cancel()
            jobs=[job for job in self.workers if job is not current]
            for job in jobs:
                if not job.done():job.cancel()
            if jobs:await asyncio.gather(*jobs,return_exceptions=True)
            if self.owner_task is not current:await self.play_settled.wait()
            self._drain(self.input);self._drain(self.audio)
            if not self._clear_started:
                self._clear_started=True
                try:
                    if await self.clear_output(self.binding) is not True:raise DeliveryError('output_cleanup_unverified')
                except BaseException:self.state='cleanup_failed';self.cleanup_verified=False;return
            self.cleanup_verified=self.state!='cleanup_failed' and all(job.done() for job in jobs)
    async def _synth_loop(self):
        while True:
            item=await self.input.get();self.require_owner()
            if item is _END:
                await self.audio.put(_END);return
            unit=self.units[item.unit_index]
            if not self.admit(unit,'synthesize'):raise DeliveryError('host_admission_revoked')
            pcm=await self.synthesize(item.text,item.language,self.binding)
            self.require_owner()
            await self.audio.put((item,pcm))
    async def _synth_owned(self):
        try:await self._synth_loop()
        except asyncio.CancelledError:raise
        except BaseException:
            self.failure='synthesis_failed';self.state='partial_failed' if self.prefix else 'failed'
            if self.owner_task is not None and not self.owner_task.done():self.owner_task.cancel()
            raise
    async def _play_loop(self):
        while True:
            item=await self.audio.get();self.require_owner()
            if item is _END:
                if self.terminal is None or self.prefix!=self.terminal.projected_text or not self.admit(self.terminal,'finalize'):raise DeliveryError('final_delivery_mismatch')
                self.finalize(self.terminal,self.prefix);return
            chunk,pcm=item
            if not self.admit(self.units[chunk.unit_index],'play'):raise DeliveryError('host_admission_revoked')
            self.play_settled.clear()
            try:await self.play(pcm,self.binding)
            finally:self.play_settled.set()
            self.require_owner()
            self.prefix+=chunk.text
            self.submit(self.prefix,chunk.language,self.binding)
    async def run(self):
        self.require_enabled()
        if self.run_started:raise DeliveryError('single_speech_owner_required')
        self.run_started=True
        # Actual pipeline.play requires current_task()==controller.task for
        # first-PCM/floor ownership. Only synthesis is delegated; playout stays
        # inline on the caller's original turn task.
        self.owner_task=asyncio.current_task();self.workers=[asyncio.create_task(self._synth_owned())]
        try:
            await self._play_loop();await asyncio.gather(*self.workers)
            self.require_owner()
            self.state='complete';self.cleanup_verified=True
        except BaseException:
            await self.abort('speech_owner_failed' if self.state=='open' else self.failure)
        return Outcome(self.state,self.prefix,''.join(u.text for u in self.units),self.terminal is not None,self.cleanup_verified)
