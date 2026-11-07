"""Explicit, source-gated normal composition of qualified advisory components.

Import/standby/prewarm never reads a key or constructs a provider transport.
The existing general Onit/Sol engine is never replaced or reconfigured.
"""
import asyncio,hashlib,json,os,signal,stat,time,uuid
from pathlib import Path
from types import SimpleNamespace
from me3_assistant.guard import verify_sources
from onit_candidate.identity import verify_candidate
from onit_candidate.floor import NativeFloorDecider,observation,FloorReviewChanged
from onit_candidate.engine import Engine,runtime,bounds
from me3_assistant.warm_floor import WarmFloorLane
from .worker import PreparedWorker,runtime_args
from .process_control import spawn_bounded,settle

SOURCE_ENABLED=True
QUALIFIED_ROLE='qwen/qwen3.8-27b advisory floor only; never the ordinary planner'
QUALIFICATION270_SHA256='578f735136a121dde1c44c20603368200350017f4decd90cc4c06da45e874c2f'
CHILD_SHA256='6c09d78855a6bb642e3ecbedb00fafcba849cf55cc6d54ac4512b38d00471ef4'
ENTRY_SHA256='50afb0d79af2fabd3c2e68a1e9ec198cff3457bcf0edb2b7415584bac38d9915'
UPSTREAM_SHA256='4a52ec2eb98fda8f05ab6d2e6b4c3895c358bc5a0fb75190efd4b8fd7a36ce59'
ROLE='qwen_floor268'
def need(value,code):
    if not value:raise ValueError(code)
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def fingerprint(source,native):return hashlib.sha256(json.dumps(dict(revision=source['revision'],native=native),sort_keys=True,separators=(',',':')).encode()).hexdigest()
def validate_switch(enabled):
    need(type(enabled) is bool,'host_floor_switch_boolean_required')
    if enabled:need(SOURCE_ENABLED is True,'source_bound_warm_floor_not_enabled')

class OneBudget:
    def __init__(self,base):self.base=base;self.calls=0
    def permitted(self,role):return role=='reasoning' and self.calls==0 and self.base.permitted(role)
    def reserve(self,role):
        need(self.permitted(role),'one_floor_capture_reservation_maximum')
        ordinal=self.base.reserve(role);self.calls+=1;return ordinal

class NativeWarmFactory:
    def __init__(self,reasoner,source):
        self.reasoner=reasoner;self.root=Path(reasoner.root).resolve();self.source=source
        self._assert_project_root();self._assert_installation();self.native=verify_candidate(self.root)
        need(verify_sources(self.root)==source,'current_session_source_required')
        self.expected=fingerprint(source,self.native);self.closed=False;self.busy=False;self.poisoned=False
        self.workers=set();self.spawns=[];self.late_cleanup=set();self.native_jobs={};self.cleanup_jobs=set();self.captures=set();self.lane=None
        self.session=Path(reasoner.events.path).resolve().parent
        need(self.session.is_relative_to(self.root/'evidence/assistant/sessions'),'actual_normal_session_evidence_required')
        self.base=self.session/'floor272'
        self.stamps=self._source_stamps()
    def _assert_project_root(self):
        from task_mcp.client import ROOT
        need(Path(ROOT).resolve()==self.root,'canonical_project_books_required')
    def _assert_installation(self):
        need(Path(__file__).resolve().parent==self.root/'me3_floor','installed_floor_runtime_required')
    def _critical_paths(self):
        return [self.root/'evidence/assistant/candidate-source.json',self.root/'onit_candidate/candidate-files.json',self.root/'onit_candidate/child.py',self.root/'onit_candidate/engine.py',self.root/'onit_candidate/floor.py',self.root/'me3_assistant/warm_floor.py',self.root/'me3_assistant/floor_admission.py',self.root/'me3_agent/overlap.py',self.root/'me3_completion/pipeline.py',Path(__file__),Path(__file__).with_name('entry.py'),Path(__file__).with_name('worker.py'),Path(__file__).with_name('transport.py'),Path(__file__).with_name('role_ledger.py')]
    def _source_stamps(self):
        value={}
        for path in self._critical_paths():
            info=path.lstat();need(stat.S_ISREG(info.st_mode) and info.st_nlink==1,'floor_source_regular_files_required')
            value[str(path)]=(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns)
        return value
    def current_source(self):
        # Only bounded metadata checks on the audio/ownership path. Full file
        # rehashes below bracket spawn, quota reservation and disposition.
        need(not self.poisoned and self._source_stamps()==self.stamps,'warm_floor_source_changed')
        return self.expected
    def _verify_full_source(self):
        self.current_source();source=verify_sources(self.root);native=verify_candidate(self.root)
        need(source==self.source and native==self.native,'warm_floor_source_changed')
        self.current_source();return self.expected
    async def verify_current(self,current):
        need(current() is True and not self.closed,'floor_owner_not_current')
        try:
            result=await asyncio.to_thread(self._verify_full_source)
        except asyncio.CancelledError:raise
        except BaseException:self.poisoned=True;raise
        need(result==self.expected and current() is True and not self.closed,'floor_owner_or_source_changed')
    def _directory(self,path,*,fresh=False):
        need(path.is_relative_to(self.base),'private_floor_path_required')
        for item in (path,*path.parents):
            if item==self.root.parent:break
            need(not item.is_symlink(),'private_floor_symlink_refused')
        try:path.mkdir(mode=0o700)
        except FileExistsError:need(not fresh,'fresh_private_floor_directory_required')
        info=path.lstat();need(stat.S_ISDIR(info.st_mode),'private_floor_directory_required')
        if os.name=='posix':need(info.st_uid==os.geteuid() and info.st_mode&0o077==0,'private_floor_directory_permissions')
    def __call__(self,processor,provider,events):
        need(provider is self.reasoner and events is self.reasoner.events and self.lane is None,'one_actual_normal_floor_owner_required')
        self.lane=WarmFloorLane(processor,provider,prepare=self.prepare,decide=self.decide,source_fingerprint=self.current_source,expected_source=self.expected)
        return self.lane
    def _build_args(self,work,nonce):
        child=self.root/'onit_candidate/child.py';entry=Path(__file__).with_name('entry.py')
        need(not child.is_symlink() and self.source['source_hashes'].get('onit_candidate/child.py')==CHILD_SHA256 and sha(child)==CHILD_SHA256,'installed_source_bound_child_required')
        need(sha(entry)==ENTRY_SHA256,'qualified_readonly_entry_required')
        return runtime_args(runtime,self.root,work,entry=entry,child=child,entry_sha256=ENTRY_SHA256,child_sha256=CHILD_SHA256,upstream_sha256=UPSTREAM_SHA256,nonce=nonce)
    async def _prepare_args(self,work,nonce):return await asyncio.to_thread(self._build_args,work,nonce)
    async def _spawn(self,args,stderr):
        return await spawn_bounded(lambda:asyncio.create_subprocess_exec(*args,stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=stderr,limit=524289,start_new_session=True,env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'},preexec_fn=bounds),self.spawns)
    def _make_worker(self,process,**kwargs):return PreparedWorker(process,**kwargs)
    async def _settle_process(self,process):return await settle(process)
    async def _wait_owned(self,job,seconds,code):
        """Repeated caller cancellation cannot cancel an already-owned cleanup."""
        deadline=asyncio.get_running_loop().time()+seconds;cancelled=False
        while not job.done():
            remaining=deadline-asyncio.get_running_loop().time()
            if remaining<=0:self.poisoned=True;raise RuntimeError(code)
            try:await asyncio.wait({job},timeout=remaining)
            except asyncio.CancelledError:cancelled=True
        return cancelled
    async def _reap_worker(self,worker):
        if not worker.adopted:await worker.close();return
        if worker.state!='CLOSED':
            need(await self._settle_process(worker.process),'floor_adopted_process_cleanup_unverified')
            worker.record_closed(worker.process)
    async def _settle_spawns(self):
        for job in self.spawns:
            if not job.done():
                job.cancel();done,pending=await asyncio.wait({job},timeout=1)
                if pending:
                    self.poisoned=True
                    def late(value):
                        if not value.cancelled() and value.exception() is None:
                            task=asyncio.create_task(self._settle_process(value.result()));self.late_cleanup.add(task)
                            task.add_done_callback(self.late_cleanup.discard)
                    job.add_done_callback(late)
                    raise RuntimeError('warm_spawn_cleanup_unverified')
            if not job.cancelled() and job.exception() is None:
                need(await self._settle_process(job.result()),'warm_actual_process_cleanup_unverified')
    async def prepare(self,*,current):
        need(not self.closed and not self.poisoned and not self.busy and current() is True,'warm_factory_owner_unavailable')
        self.workers={w for w in self.workers if w.state!='CLOSED'}
        self.spawns=[j for j in self.spawns if not j.done() or (not j.cancelled() and j.exception() is None and j.result().returncode is None)]
        need(not self.workers and not self.spawns and not self.late_cleanup and not self.native_jobs and not self.cleanup_jobs,'one_warm_child_maximum')
        self.busy=True;process=None;worker=None
        try:
            await self.verify_current(current);self._directory(self.base)
            folder=self.base/('p-'+uuid.uuid4().hex);self._directory(folder,fresh=True)
            nonce=uuid.uuid4().hex;work=self.root/'.tmp'/('onit-floor272-'+nonce)
            created=time.monotonic();args=await self._prepare_args(work,nonce)
            need(current() is True and not self.closed,'warm_owner_changed_before_spawn')
            fd=os.open(folder/'child-stderr.txt',os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
            with os.fdopen(fd,'wb') as err:process=await self._spawn(args,err)
            worker=self._make_worker(process,created_at=created,work=work,stderr_path=folder/'child-stderr.txt',nonce=nonce,child_sha256=CHILD_SHA256,upstream_sha256=UPSTREAM_SHA256,source_fingerprint=self.expected,current_source_fingerprint=self.current_source,current_owner=lambda:not self.closed and current() is True)
            self.workers.add(worker);worker.floor_folder=folder
            await worker.wait_ready();need(current() is True and not self.closed,'warm_owner_changed_after_READY')
            self.reasoner.events.emit('floor_worker_READY',ready_seconds=worker.ready_seconds,model_requests=0,keys_read=0,action_authority=False)
            return worker
        except BaseException:
            if worker is not None and not worker.adopted:await worker.close()
            elif process is not None:need(await self._settle_process(process),'warm_actual_process_cleanup_unverified')
            await self._settle_spawns();raise
        finally:self.busy=False
    def _transport(self,folder):
        # This is called only after exact current recognized capture admission.
        from me3_assistant.backends import groq_key
        from .transport import Transport,RoleLedger,LEDGER_NAME
        return Transport(self.reasoner.events.emit,key=groq_key(self.root/'.env.groq'),opt_in=True,ledger=RoleLedger(folder/'provider-admission'/LEDGER_NAME))
    def _engine(self,worker,current):
        factory=self
        class BoundEngine(Engine):
            def __init__(self,*args,**kwargs):
                super().__init__(*args,prepared_worker=worker,**kwargs)
                self.before_floor_reservation=lambda:factory.verify_current(current)
            async def answer(self,*args,**kwargs):
                job=asyncio.create_task(super().answer(*args,**kwargs));factory.native_jobs[job]=self
                try:return await asyncio.shield(job)
                except asyncio.CancelledError:
                    if not job.done() and not self.stop.is_set():job.cancel()
                    try:await factory._wait_owned(job,5,'floor_native_cleanup_unsettled')
                    except RuntimeError:
                        cleanup=asyncio.create_task(factory._reap_worker(worker));factory.cleanup_jobs.add(cleanup)
                        await factory._wait_owned(cleanup,5,'floor_forced_cleanup_unsettled');cleanup.result();factory.cleanup_jobs.discard(cleanup)
                        await factory._wait_owned(job,2,'floor_native_owner_unsettled_after_exit')
                    raise
                finally:
                    if job.done():factory.native_jobs.pop(job,None)
        return BoundEngine
    async def _cleanup_capture(self,tr,worker):
        try:
            if tr is not None:
                try:tr.end_goal()
                finally:await tr.close()
        finally:
            await self._reap_worker(worker)
            need(worker.state=='CLOSED','floor_decision_child_cleanup_unverified')
    async def decide(self,worker,value,*,current):
        value=observation(value);r=self.reasoner
        need(not self.closed and not self.poisoned and worker in self.workers and worker.state=='READY' and not worker.adopted,'owned_unused_READY_required')
        # The advisory Engine has its own tool-free Registry and Transport.
        # Bind the live main lease; never borrow or reconfigure its transport.
        main=r.engine;generation=main.generation;owner=main.task;transport=main.transport
        parent_current=current;settled=owner is None
        def current():
            nonlocal settled
            if (self.closed or r.engine is not main or main.generation!=generation or main.transport is not transport):return False
            if r.pending is not None:raise FloorReviewChanged('floor_capture_predates_new_review')
            if parent_current() is not True:return False
            if main.task is None:settled=True;return True
            return bool(not settled and main.task is owner)
        need(current() is True,'current_bound_floor_capture_required')
        need(value['capture_id'] not in self.captures and len(self.captures)<128,'floor_capture_consumed_or_limit')
        self.captures.add(value['capture_id'])
        await self.verify_current(current);self._assert_project_root()
        folder=worker.floor_folder/('c-'+value['capture_id']);self._directory(folder,fresh=True)
        tr=None;budget=OneBudget(r.engine.budget)
        try:
            need(current() is True and r.pending is None,'current_floor_before_key_required')
            tr=self._transport(folder)
            need(tr is not transport,'separate_advisory_transport_required')
            need(tr.combined.base.path==self.root/'evidence/assistant/provider-admission/groq.json' and tr.combined.output.path==self.root/'evidence/assistant/provider-admission/onit-output.sqlite3','canonical_Groq_OutputQuota_authorities_required')
            isolated=SimpleNamespace(root=self.root,events=r.events,pending=None,engine=SimpleNamespace(task=None,transport=tr,budget=budget,registry=r.engine.registry))
            selected=await NativeFloorDecider(isolated,timeout_seconds=15,engine_factory=self._engine(worker,current),qualification_role=ROLE)(value,current=lambda:current() is True and r.pending is None and not self.closed)
            need(budget.calls==1 and worker.state=='CLOSED','one_native_capture_and_cleanup_required')
            await self.verify_current(current)
            return selected
        finally:
            cleanup=asyncio.create_task(self._cleanup_capture(tr,worker));self.cleanup_jobs.add(cleanup)
            cancelled=await self._wait_owned(cleanup,5,'floor_capture_cleanup_unsettled');cleanup.result();self.cleanup_jobs.discard(cleanup)
            if cancelled:raise asyncio.CancelledError()
    async def close(self):
        self.closed=True;cancelled=False
        if self.lane is not None:await self.lane.close()
        for job,engine in list(self.native_jobs.items()):
            if not job.done() and not engine.stop.is_set():job.cancel()
            cancelled=await self._wait_owned(job,5,'floor_native_owner_cleanup_unverified') or cancelled
            if job.done():self.native_jobs.pop(job,None)
        for job in list(self.cleanup_jobs):
            cancelled=await self._wait_owned(job,5,'floor_capture_cleanup_unverified') or cancelled;job.result();self.cleanup_jobs.discard(job)
        for worker in self.workers:
            if not worker.adopted:await worker.close()
            need(worker.state=='CLOSED','floor_actual_worker_exit_required')
        await self._settle_spawns()
        if self.late_cleanup:
            done,pending=await asyncio.wait(self.late_cleanup,timeout=5)
            need(not pending and all(t.exception() is None and t.result() is True for t in done),'late_owned_process_cleanup_unverified')
        need(not self.poisoned,'warm_factory_cleanup_previously_unverified')
        if cancelled:raise asyncio.CancelledError()

def configure_normal_floor(reasoner,source,*,enabled=False):
    validate_switch(enabled)
    if not enabled:return None
    need(getattr(reasoner,'floor_decider',None) is None and getattr(reasoner,'warm_floor_factory',None) is None,'single_floor_factory_required')
    factory=NativeWarmFactory(reasoner,source);reasoner.warm_floor_factory=factory;return factory
