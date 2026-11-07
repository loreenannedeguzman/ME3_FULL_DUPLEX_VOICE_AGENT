"""Inactive, one-use MAIN import preparation owned by one actually dispatched ASR.

No transcript, goal/init, credentials, transport/model or schema enters a prepared
process. The original Engine alone sends its current validated init after ASR.
No prepared failure can fall back to a second cold process for the same goal.
"""
import asyncio,hashlib,json,os,re,stat,time,uuid
from pathlib import Path
from types import SimpleNamespace
from me3_floor.worker import PreparedWorker,runtime_args
from me3_floor.process_control import settle
ROLE='main_onit_imports296'
SOURCE_ENABLED=False
CHILD_SHA256='6c09d78855a6bb642e3ecbedb00fafcba849cf55cc6d54ac4512b38d00471ef4'
UPSTREAM_SHA256='4a52ec2eb98fda8f05ab6d2e6b4c3895c358bc5a0fb75190efd4b8fd7a36ce59'
PREPARE_SECONDS=3.0
IDLE_SECONDS=40.0 # Existing actual ASR RPC36s; no unrelated task/goal reuse.
HANDOFF_SECONDS=3.0
MIN_AVAILABLE_BYTES=2415919104 # Existing2GiB address-space bound plus256MiB margin.
def need(value,code):
    if not value:raise ValueError(code)
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def enabled(value):
    need(type(value) is bool,'main_prewarm_switch_boolean_required')
    if value:need(SOURCE_ENABLED is True,'main_prewarm_source_not_enabled')
def ready_frame(raw,*,nonce,child_sha256,upstream_sha256):
    need(type(raw) is bytes and 0<len(raw)<=2048 and raw.endswith(b'\n'),'bounded_main_READY_required')
    def pairs(items):
        value={}
        for key,item in items:need(key not in value,'duplicate_main_READY_key');value[key]=item
        return value
    row=json.loads(raw,object_pairs_hook=pairs)
    expected=dict(v=1,sequence=0,kind='native_imports_ready',nonce=nonce,child_sha256=child_sha256,upstream_sha256=upstream_sha256,role=ROLE,request_received=False)
    need(row==expected and type(row.get('v')) is int and type(row.get('sequence')) is int and row.get('request_received') is False,'exact_MAIN_READY_identity_required')
    return row
def resources(cpu_count,available_bytes):
    need(type(cpu_count) is int and type(available_bytes) is int and cpu_count>=0 and available_bytes>=0,'main_resource_snapshot_required')
    return cpu_count>=2 and available_bytes>=MIN_AVAILABLE_BYTES
def resource_snapshot():
    need(os.name=='posix' and Path('/proc/meminfo').is_file(),'main_POSIX_resource_snapshot_required')
    raw=Path('/proc/meminfo').read_bytes();need(len(raw)<=16384,'bounded_meminfo_required')
    line=next((v for v in raw.decode('ascii').splitlines() if v.startswith('MemAvailable:')),None)
    need(line is not None and re.fullmatch(r'MemAvailable:\s+[0-9]+ kB',line) is not None,'main_memory_snapshot_required')
    return len(os.sched_getaffinity(0)),int(line.split()[1])*1024
class MainWorker(PreparedWorker):
    """Reuse unchanged process ownership, but never the Qwen READY role."""
    async def close(self):
        if not hasattr(self,'_close_job'):self._close_job=asyncio.create_task(super().close())
        end=asyncio.get_running_loop().time()+6
        while not self._close_job.done():
            remaining=end-asyncio.get_running_loop().time()
            if remaining<=0:self.state='FAILED';raise ValueError('MAIN_worker_cleanup_unverified')
            try:await asyncio.wait({self._close_job},timeout=remaining)
            except asyncio.CancelledError:continue
        return self._close_job.result()
    async def wait_ready(self):
        need(self.state=='STARTING','main_READY_once_required');self.state='WAITING_READY'
        try:
            if self.owner() is not True:raise asyncio.CancelledError()
            need(self.current(),'main_owner_or_source_changed')
            self.read_task=asyncio.create_task(self.process.stdout.readline())
            done,pending=await asyncio.wait({self.read_task},timeout=max(0,PREPARE_SECONDS-(self.clock()-self.created)))
            need(not pending,'main_preparation_deadline')
            ready_frame(self.read_task.result(),nonce=self.nonce,child_sha256=self.child_sha256,upstream_sha256=self.upstream_sha256)
            if self.owner() is not True:raise asyncio.CancelledError()
            need(self.current(),'main_owner_or_source_changed')
            self.ready_at=self.clock();self.ready_seconds=self.ready_at-self.created;self.state='READY'
            return dict(imports_ready=True,role=ROLE,ready_seconds=self.ready_seconds,request_received=False)
        except BaseException:await self.close();raise
    async def acquire(self,*,current):
        if self.owner() is not True or current() is not True:raise asyncio.CancelledError()
        need(self.ready_at is not None and 0<=self.clock()-self.ready_at<=IDLE_SECONDS,'main_prepared_idle_expired')
        return await super().acquire(current=current)
class MainPrewarm:
    def __init__(self,reasoner,source,controller,*,on_failure):
        self.engine=reasoner.engine;self.events=reasoner.events;self.root=Path(reasoner.root).resolve();self.controller=controller;self.source=source;self.on_failure=on_failure
        need(self.engine.provider=='openai_chatgpt_plan' and self.engine.model=='gpt-6.1-sol' and self.engine.floor_qualification_role is None and self.engine.prepared_worker is None,'ordinary_MAIN_engine_only')
        need(Path(__file__).resolve()==self.root/'onit_candidate/main_prewarm.py','installed_MAIN_preparation_required')
        from me3_assistant.guard import verify_sources
        from onit_candidate.identity import verify_candidate
        need(verify_sources(self.root)==source,'current_MAIN_source_required');self.native=verify_candidate(self.root)
        self.fingerprint=hashlib.sha256(json.dumps(dict(revision=source['revision'],native=self.native),sort_keys=True,separators=(',',':')).encode()).hexdigest()
        self.paths=sorted({self.root/name for name in source['source_hashes']}|{self.root/'evidence/assistant/candidate-source.json',self.root/'onit_candidate/candidate-files.json',self.root/'evidence/assistant/operating-authorization.json',self.root/'config/operating_policy.json'})
        self.stamps=self._stamps();self.record=None;self.jobs=set();self.closed=False;self.poisoned=False;self.last_asr_ordinal=0;self.answer_bound=False
        self.base=Path(self.events.path).resolve().parent/'main296'
        need(self.base.parent.is_relative_to(self.root/'evidence/assistant/sessions'),'actual_MAIN_session_evidence_required')
    def _stamps(self):
        value=[]
        for path in self.paths:
            info=path.lstat();need(stat.S_ISREG(info.st_mode) and info.st_nlink==1,'MAIN_source_regular_required')
            value.append((info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns))
        return tuple(value)
    def current_source(self):
        need(not self.poisoned and self._stamps()==self.stamps,'MAIN_source_changed');return self.fingerprint
    def current(self,record):return not self.closed and self.record is record and self.controller.current(record.generation) is True
    def _directory(self,path,*,fresh=False):
        path=Path(path);need(path.is_relative_to(self.root),'MAIN_owned_directory_required')
        for item in (path,*path.parents):
            if item==self.root:break
            need(not item.is_symlink(),'MAIN_directory_symlink_refused')
        try:path.mkdir(mode=0o700)
        except FileExistsError:need(not fresh,'MAIN_fresh_directory_required')
        info=path.lstat();need(stat.S_ISDIR(info.st_mode) and info.st_uid==os.geteuid() and info.st_mode&0o077==0,'MAIN_private_directory_required')
    def _build(self,work,nonce):
        from me3_assistant.guard import verify_sources
        from onit_candidate.identity import verify_candidate
        from onit_candidate.engine import runtime
        self.current_source();need(verify_sources(self.root)==self.source and verify_candidate(self.root)==self.native,'MAIN_current_source_required')
        child=self.root/'onit_candidate/child.py';entry=self.root/'onit_candidate/main_import_entry.py'
        need(sha(child)==CHILD_SHA256 and self.source['source_hashes'].get('onit_candidate/child.py')==CHILD_SHA256 and self.source['source_hashes'].get('onit_candidate/main_import_entry.py')==sha(entry),'MAIN_installed_entry_child_required')
        return runtime_args(runtime,self.root,work,entry=entry,child=child,entry_sha256=sha(entry),child_sha256=CHILD_SHA256,upstream_sha256=UPSTREAM_SHA256,nonce=nonce)
    def _track(self,coro):
        task=asyncio.create_task(coro);self.jobs.add(task)
        task.add_done_callback(lambda done:None if done.cancelled() else done.exception())
        return task
    async def _wait(self,job,seconds,code):
        end=asyncio.get_running_loop().time()+seconds
        while not job.done():
            remaining=end-asyncio.get_running_loop().time()
            if remaining<=0:self.poisoned=True;raise ValueError(code)
            try:await asyncio.wait({job},timeout=remaining)
            except asyncio.CancelledError:continue
        return job.result()
    async def _prepare(self,record):
        from onit_candidate.engine import bounds
        try:
            nonce=uuid.uuid4().hex;work=self.root/'.tmp'/('onit-main296-'+nonce);self._directory(self.base)
            folder=self.base/nonce;self._directory(folder,fresh=True)
            started=time.monotonic();record.folder=folder
            build=self._track(asyncio.to_thread(self._build,work,nonce));record.build=build
            done,pending=await asyncio.wait({build},timeout=PREPARE_SECONDS)
            need(not pending,'MAIN_build_deadline');args=build.result()
            if not self.current(record):raise asyncio.CancelledError()
            need(self.current_source()==self.fingerprint and time.monotonic()-started<PREPARE_SECONDS,'MAIN_source_or_deadline_before_spawn')
            fd=os.open(folder/'child-stderr.txt',os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
            with os.fdopen(fd,'wb') as err:
                spawn=self._track(asyncio.create_subprocess_exec(*args,stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=err,limit=524289,start_new_session=True,env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'},preexec_fn=bounds));record.spawn=spawn
                done,pending=await asyncio.wait({spawn},timeout=max(0,PREPARE_SECONDS-(time.monotonic()-started)))
                need(not pending,'MAIN_spawn_deadline');process=spawn.result()
            record.worker=MainWorker(process,created_at=started,work=work,stderr_path=folder/'child-stderr.txt',nonce=nonce,child_sha256=CHILD_SHA256,upstream_sha256=UPSTREAM_SHA256,source_fingerprint=self.fingerprint,current_source_fingerprint=self.current_source,current_owner=lambda:self.current(record))
            await record.worker.wait_ready()
            if not self.current(record):raise asyncio.CancelledError()
            self.events.emit('onit_MAIN_imports_READY',generation=record.generation,asr_ordinal=record.asr_ordinal,ready_seconds=record.worker.ready_seconds,model_requests=0,request_received=False,role=ROLE)
            return record.worker
        except BaseException:
            await self._cleanup_record(record);raise
    def _prep_done(self,record,job):
        if job.cancelled():return
        if job.exception() is not None:
            self.poisoned=True;self.events.emit('onit_MAIN_preparation_failed',generation=record.generation,error_type=type(job.exception()).__name__,automatic_retry=False,model_requests=0);self.on_failure()
    async def start_asr(self,generation,ordinal):
        need(not self.closed and not self.poisoned,'MAIN_preparation_failed_no_cold_retry')
        if not self.controller.current(generation):return None
        need(type(ordinal) is int and ordinal>0,'actual_ASR_reservation_required')
        need(ordinal>getattr(self,'last_asr_ordinal',0),'MAIN_new_ASR_reservation_required')
        self.last_asr_ordinal=ordinal
        if self.record is not None:
            # A later reserved capture may extend the same semantic turn. The
            # old process remains owned until verified cleanup; never recycle it.
            try:await self._cleanup_record(self.record)
            except BaseException:self.poisoned=True;raise
            self.record=None
        need(not self.closed and not self.poisoned,'MAIN_preparation_failed_no_cold_retry')
        if not self.controller.current(generation):return None
        cpu,memory=resource_snapshot()
        if not resources(cpu,memory):
            self.events.emit('onit_MAIN_preparation_skipped',generation=generation,reason='conservative_resource_admission',prepared_process_started=False,cold_original_path=True)
            return None
        record=SimpleNamespace(generation=generation,asr_ordinal=ordinal,asr_complete=False,worker=None,build=None,spawn=None,prepare=None,expiry=None,adopted=False,finished=False)
        self.record=record;record.prepare=self._track(self._prepare(record));record.prepare.add_done_callback(lambda job:self._prep_done(record,job))
        return record
    def asr_complete(self,record):
        if record is None or self.record is not record:return
        if not self.current(record):raise asyncio.CancelledError()
        need(not record.asr_complete and not record.finished,'MAIN_ASR_completion_current_once_required');record.asr_complete=True
        async def expire():
            await asyncio.sleep(HANDOFF_SECONDS)
            if self.record is record and not record.adopted:
                await self._cleanup_record(record);record.retired='unused_expired'
        record.expiry=self._track(expire())
    async def asr_failed(self,record):
        if record is not None and self.record is record:
            try:await self._cleanup_record(record)
            except BaseException:self.poisoned=True;raise
            record.retired='ASR_failed'
    def bind_answer(self,reasoner):
        need(not getattr(self,'answer_bound',False) and reasoner.engine is self.engine,'MAIN_answer_owner_once_required')
        original=reasoner.answer;self.answer_bound=True
        async def answer(*args,**kwargs):
            record=self.record;generation=reasoner.generation()
            try:return await original(*args,**kwargs)
            finally:await self.answer_closed(record,generation)
        reasoner.answer=answer
    async def answer_closed(self,record,generation):
        # Capture identity at answer entry. An old answer cannot retire a later
        # same-generation ASR reservation or a new controller generation.
        if record is None or self.record is not record or record.generation!=generation or record.adopted:return
        try:await self._cleanup_record(record)
        except BaseException:self.poisoned=True;raise
        if not getattr(record,'retired',None):record.retired='unused_answer_closed'
        if self.record is record:self.record=None
    def _unused_expired_closed(self,record):
        need(record.finished and getattr(record,'retired',None)=='unused_expired' and not record.adopted and not hasattr(record,'goal_id'),'MAIN_unused_first_dispatch_only')
        need(record.prepare is not None and record.prepare.done() and (record.prepare.cancelled() or record.prepare.exception() is None),'MAIN_unused_preparation_cleanup_required')
        if record.worker is not None:need(record.worker.state=='CLOSED' and record.worker.process.returncode is not None,'MAIN_unused_process_exit_required')
        if record.spawn is not None:need(record.spawn.done() and not record.spawn.cancelled() and record.spawn.exception() is None and record.spawn.result().returncode is not None,'MAIN_unused_spawn_exit_required')
    async def take(self,*,goal_id,engine_generation,current):
        need(not self.closed and not self.poisoned and self.engine.main_prewarm is self,'MAIN_preparation_failed_no_cold_retry')
        record=self.record
        if record is None:return None # No prepared process was attempted (typed/recognized-overlap/resource skip).
        if not self.controller.current(record.generation):
            # A new explicit typed/recognized-overlap goal may have no ASR of
            # its own. Retire the old capture, never transfer its warm process.
            await self._cleanup_record(record);self.record=None;return None
        if not self.current(record) or current() is not True:raise asyncio.CancelledError()
        if record.finished and getattr(record,'retired',None)=='unused_expired':
            self._unused_expired_closed(record)
            self.events.emit('onit_MAIN_unused_preparation_retired',generation=record.generation,asr_ordinal=record.asr_ordinal,first_goal_dispatch=True,model_requests_before_retirement=0,cold_retry=False)
            self.record=None;return None
        need(record.asr_complete and not record.adopted and not record.finished,'committed_current_ASR_MAIN_once_required')
        if record.expiry is not None:
            record.expiry.cancel()
            try:await self._wait(record.expiry,1,'MAIN_expiry_cleanup_unverified')
            except asyncio.CancelledError:pass
        need(not record.finished,'MAIN_expired_preparation_not_adoptable')
        record.goal_id=goal_id;record.engine_generation=engine_generation
        worker=await asyncio.shield(record.prepare)
        need(self.current(record) and current() is True and worker.state=='READY','current_MAIN_READY_required')
        record.adopted=True;record.goal_id=goal_id;record.engine_generation=engine_generation
        return worker
    async def goal_closed(self,goal_id,worker):
        record=self.record
        if record is None or getattr(record,'goal_id',None)!=goal_id:return
        if record.adopted:
            need(record.worker is worker and worker.state=='CLOSED','MAIN_goal_cleanup_unverified');record.finished=True
        else:await self._cleanup_record(record)
    async def _cleanup_record(self,record):
        if record.adopted:
            if record.worker.state!='CLOSED':
                task=self.engine.task
                if task is not None:
                    # The normal controller owns invalidation/confirmation state.
                    # This import manager never cancels a live goal on its own.
                    try:await self._wait(task,6,'MAIN_adopted_cleanup_unverified')
                    except asyncio.CancelledError:pass
            need(record.worker.state=='CLOSED','MAIN_adopted_cleanup_unverified');return
        for name in ('expiry','prepare'):
            task=getattr(record,name,None)
            if task is not None and task is not asyncio.current_task() and not task.done():task.cancel()
        task=record.prepare
        if task is not None and task is not asyncio.current_task() and not task.done():
            try:await self._wait(task,6,'MAIN_prepare_cleanup_unverified')
            except asyncio.CancelledError:pass
        for name in ('build','spawn'):
            job=getattr(record,name,None)
            if job is not None and not job.done():await self._wait(job,3,'MAIN_creation_cleanup_unverified')
        if record.worker is not None:
            if record.worker.state!='CLOSED':await record.worker.close()
        elif record.spawn is not None and record.spawn.done() and not record.spawn.cancelled() and record.spawn.exception() is None:
            need(await settle(record.spawn.result()),'MAIN_actual_process_cleanup_unverified')
        record.finished=True
    async def close(self):
        if self.closed:
            need(not self.poisoned,'MAIN_cleanup_previous_failure');return
        self.closed=True
        try:
            if self.record is not None:await self._cleanup_record(self.record)
            for job in self.jobs:
                if not job.done():await self._wait(job,6,'MAIN_owned_job_cleanup_unverified')
        except BaseException:self.poisoned=True;raise
