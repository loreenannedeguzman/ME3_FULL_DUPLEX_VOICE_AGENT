"""One owned native inference at a time; canceled callers cannot mutate its input."""
import asyncio,time,math
class LocalEndpoint:
    def __init__(self,events,analyzer=None,deadline=2,minimum_complete_score=.75):
        if not .1<=deadline<=5:raise ValueError('invalid_endpoint_deadline')
        if analyzer is None:
            from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
            analyzer=LocalSmartTurnAnalyzerV3(cpu_count=1)
        if not .5<=minimum_complete_score<=.95:raise ValueError('invalid_endpoint_score_floor')
        self.minimum_complete_score=minimum_complete_score
        self.analyzer=analyzer;analyzer._log_data=False;analyzer.set_sample_rate(16000)
        analyzer.update_vad_start_secs(.15)
        self.events=events;self.deadline=deadline;self.lock=asyncio.Lock();self.jobs=set();self.closed=False
    async def _run(self,pcm,token):
        async with self.lock:
            if self.closed:raise RuntimeError('endpoint_closed')
            self.analyzer.clear();self.analyzer.append_audio(pcm,True);start=time.monotonic_ns()
            self.events.emit('completion_endpoint_start',token=token,pcm_bytes=len(pcm))
            state,metric=await self.analyzer.analyze_end_of_turn()
            score=getattr(metric,'probability',None)
            if type(score) not in (int,float) or not math.isfinite(score) or not 0<=score<=1:raise ValueError('invalid_endpoint_score')
            complete=state.name=='COMPLETE' and score>=self.minimum_complete_score
            self.events.emit('completion_endpoint_result',token=token,complete=complete,raw_complete=state.name=='COMPLETE',minimum_complete_score=self.minimum_complete_score,inference_ns=time.monotonic_ns()-start,probability=getattr(metric,'probability',None),measurement='software local inference; no audio retained')
            return complete
    async def decide(self,pcm,token):
        if self.closed:raise RuntimeError('endpoint_closed')
        if not isinstance(pcm,bytes) or not 3200<=len(pcm)<=480000 or len(pcm)%2:raise ValueError('invalid_endpoint_pcm')
        job=asyncio.create_task(self._run(pcm,token));self.jobs.add(job)
        def done(task):
            self.jobs.discard(task)
            if not task.cancelled():task.exception() # retrieve errors even after the caller was canceled
        job.add_done_callback(done)
        # Shield owns the native job after caller cancellation. New jobs serialize on lock.
        try:return await asyncio.wait_for(asyncio.shield(job),self.deadline)
        except asyncio.CancelledError:
            self.events.emit('completion_endpoint_caller_canceled',token=token,native_job_owned_until_completion=True);raise
    async def close(self):
        self.closed=True;jobs=list(self.jobs)
        try:
            if jobs:await asyncio.wait_for(asyncio.shield(asyncio.gather(*jobs,return_exceptions=True)),3)
        except TimeoutError:
            self.events.emit('completion_endpoint_cleanup_timeout',pending=len(self.jobs),native_compute_killed=False);raise
        finally:await self.analyzer.cleanup()
