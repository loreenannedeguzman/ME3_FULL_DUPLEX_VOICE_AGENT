"""Inactive, injected one-worker response lifecycle. No spawning/provider factory.

prepare(current=...) owns cancellation-safe bounded worker creation; decide
must use the original one-request native Engine and existing quota authorities.
Neither hook is supplied by normal GUI construction in this candidate.
"""
import asyncio
from onit_candidate.floor import FloorError,choice,digest,observation

class WarmFloorUnavailable(FloorError):pass
READY_HANDOFF_SECONDS=2.0 # Extra wait only; not a whole semantic-decision bound.
ADVISORY_DECISION_SECONDS=15

class WarmFloorLane:
    def __init__(self,processor,provider,*,prepare,decide,source_fingerprint,expected_source):
        if not all(callable(v) for v in (prepare,decide,source_fingerprint)):raise ValueError('explicit_warm_floor_dependencies_required')
        self.p=processor;self.provider=provider;self.prepare=prepare;self.decide=decide
        self.source=source_fingerprint;self.expected_source=expected_source
        self.record=None;self.worker=None;self.preparing=None;self.deciding=None
        self.retirement=None;self.expiry=None;self.waiting=None;self.epoch=0;self.closed=False;self.seen=set()
    def fresh(self,record,token=None):
        p=self.p
        return bool(not self.closed and not p.closing and not p.pipeline_failed.is_set()
            and record is not None and p.floor_response is record and record['started']
            and p.floor_response_task(record['generation']) is record['task']
            and getattr(self.provider,'pending',None) is None
            and self.source()==self.expected_source and (token is None or token==p.token))
    def _spawn(self,coroutine):
        job=asyncio.create_task(coroutine)
        def done(value):
            if not value.cancelled() and value.exception() is not None:self.p.pipeline_failed.set()
        job.add_done_callback(done);return job
    async def _close_worker(self,worker):
        if worker is None:return
        if not worker.adopted:await worker.close()
        if worker.state!='CLOSED':raise FloorError('warm_floor_worker_cleanup_unverified')
    async def _retire(self,jobs,worker):
        try:
            async with asyncio.timeout(5):
                if jobs:
                    done,pending=await asyncio.wait(jobs,timeout=5)
                    if pending:raise FloorError('warm_floor_retirement_unsettled')
                    for job in done:
                        if not job.cancelled() and job.exception() is not None:raise FloorError('warm_floor_retirement_failed')
                await self._close_worker(worker)
        except BaseException:self.p.pipeline_failed.set();raise
    def invalidate(self,record=None):
        if record is not None and self.record is not record:return
        self.epoch+=1;self.record=None;worker=self.worker;self.worker=None
        current=asyncio.current_task();jobs={j for j in (self.preparing,self.deciding,self.expiry,self.waiting,self.retirement) if j is not None and not j.done() and j is not current}
        # Retire prior cleanup rather than canceling it; prevent a second child.
        for job in jobs:
            if job is not self.retirement:job.cancel()
        self.preparing=None;self.deciding=None;self.expiry=None
        self.retirement=self._spawn(self._retire(jobs,worker))
    def first_pcm(self,record):
        if not self.fresh(record) or self.preparing is not None or self.worker is not None or self.deciding is not None:return
        self.record=record;epoch=self.epoch
        self.preparing=self._spawn(self._prepare(record,epoch))
    async def _prepare(self,record,epoch):
        worker=None
        try:
            if self.retirement is not None:await asyncio.shield(self.retirement)
            if epoch!=self.epoch or not self.fresh(record):return
            async with asyncio.timeout(30):
                worker=await self.prepare(current=lambda:epoch==self.epoch and self.fresh(record))
            if epoch!=self.epoch or not self.fresh(record):return
            if worker.state!='READY' or worker.adopted:raise FloorError('exact_unused_READY_required')
            self.worker=worker;self.expiry=self._spawn(self._expire(worker));worker=None
        finally:
            await self._close_worker(worker)
            if self.preparing is asyncio.current_task():self.preparing=None
    async def _expire(self,worker):
        record=self.record;epoch=self.epoch
        await asyncio.sleep(max(0,60-(worker.clock()-worker.ready_at)))
        if self.worker is worker:
            async with asyncio.timeout(5):await self._close_worker(worker)
            if self.worker is worker:
                self.worker=None
                if self.expiry is asyncio.current_task():self.expiry=None
                # A long current response keeps a fresh unused worker, never a
                # reused process or a second concurrent child. No model/quota.
                if epoch==self.epoch and self.record is record and self.fresh(record):self.first_pcm(record)
    async def _await_owned_ready(self,record,value,current):
        epoch=self.epoch;deadline=asyncio.get_running_loop().time()+READY_HANDOFF_SECONDS
        if self.waiting is not None:raise FloorError('warm_floor_ready_handoff_already_active')
        self.waiting=asyncio.current_task()
        try:
            while True:
                if epoch!=self.epoch or self.record is not record or current() is not True or not self.fresh(record,value['token']):
                    raise WarmFloorUnavailable('warm_floor_ready_handoff_stale')
                worker=self.worker;job=self.preparing
                if job is None and worker is not None and not worker.adopted and worker.clock()-worker.ready_at>=60:
                    job=self.expiry # Only the existing timer owns close/refresh.
                if job is None:return
                if job.done():
                    if job.cancelled() or job.exception() is not None:raise FloorError('warm_floor_ready_refresh_failed')
                    return
                remaining=deadline-asyncio.get_running_loop().time()
                if remaining<=0:raise WarmFloorUnavailable('warm_floor_ready_handoff_timeout')
                done,pending=await asyncio.wait({job},timeout=remaining)
                if pending:raise WarmFloorUnavailable('warm_floor_ready_handoff_timeout')
                if job.cancelled() or job.exception() is not None:raise FloorError('warm_floor_ready_refresh_failed')
                # A completed expiry may already have scheduled its one fresh
                # preparation; loop without spawning, retrying or changing age.
        finally:
            if self.waiting is asyncio.current_task():self.waiting=None
    async def __call__(self,value,*,current):
        value=observation(value);record=self.record
        if (callable(current) and current() is True and self.fresh(record,value['token'])
            and not value['pending_review'] and value['pending_fingerprint'] is None
            and value['generation']==record['generation'] and self.deciding is None):
            await self._await_owned_ready(record,value,current)
        worker=self.worker
        if (not callable(current) or current() is not True or not self.fresh(record,value['token'])
            or value['pending_review'] or value['pending_fingerprint'] is not None
            or record is None or value['generation']!=record['generation'] or self.preparing is not None
            or self.deciding is not None or worker is None or worker.state!='READY' or worker.adopted
            or not 0<=worker.clock()-worker.ready_at<=60):
            self.invalidate();raise WarmFloorUnavailable('warm_floor_not_READY_no_cold_fallback')
        if value['capture_id'] in self.seen or len(self.seen)>=128:raise FloorError('warm_floor_capture_consumed_or_limit')
        self.seen.add(value['capture_id']);self.worker=None;self.deciding=asyncio.current_task()
        epoch=self.epoch;fresh=lambda:epoch==self.epoch and current() and self.fresh(record,value['token'])
        canceled=False;deadline=asyncio.timeout(ADVISORY_DECISION_SECONDS);deadline_expired=False;decider_failed=False
        try:
            expiry=self.expiry;self.expiry=None
            if expiry is not None:
                expiry.cancel()
                done,pending=await asyncio.wait({expiry},timeout=1)
                if pending:raise FloorError('warm_floor_idle_timer_cleanup_unsettled')
            async with deadline:
                try:result=await self.decide(worker,value,current=fresh)
                except Exception:
                    decider_failed=True;raise
            if not fresh():raise FloorError('warm_floor_result_stale')
            return choice(result,digest(value),pending_review=False)
        except asyncio.CancelledError:
            canceled=True;raise
        except TimeoutError:
            if decider_failed or not deadline.expired():raise
            deadline_expired=True
        finally:
            try:
                async with asyncio.timeout(5):await self._close_worker(worker)
            except BaseException:self.p.pipeline_failed.set();raise
            finally:
                if self.deciding is asyncio.current_task():self.deciding=None
            # A newer capture may cancel this decision while retaining the
            # exact speaking response. Prepare only after verified cleanup;
            # never retry this consumed capture or reuse its worker/result.
            if canceled and epoch==self.epoch and self.record is record and self.fresh(record):self.first_pcm(record)
        # Only our deadline is advisory unavailability, and only after the
        # existing cleanup verified CLOSED. Never replay or infer a disposition.
        if deadline_expired:raise WarmFloorUnavailable('warm_floor_advisory_deadline_no_retry')
    def resumed(self):
        # Called only after actual host admission resumed the same response.
        self.first_pcm(self.p.floor_response)
    def retained_turn(self):
        # Same response remains suspended; prepare only, never resume output.
        self.first_pcm(self.p.floor_response)
    async def close(self):
        self.closed=True;self.invalidate()
        if self.retirement is not None:await asyncio.shield(self.retirement)
