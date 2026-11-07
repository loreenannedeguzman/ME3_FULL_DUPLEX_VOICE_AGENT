"""Single-use host worker ownership; no spawning, model, DNS or credentials here."""
import asyncio,hashlib,json,math,os,re,signal,time
from pathlib import Path
def need(v,c):
    if not v:raise ValueError(c)
def ready_frame(raw,*,nonce,child_sha256,upstream_sha256):
    need(type(raw) is bytes and 0<len(raw)<=2048 and raw.endswith(b'\n'),'bounded_ready_frame_required')
    def pairs(items):
        out={}
        for k,v in items:
            need(k not in out,'duplicate_ready_key');out[k]=v
        return out
    value=json.loads(raw,object_pairs_hook=pairs)
    expected=dict(v=1,sequence=0,kind='native_imports_ready',nonce=nonce,child_sha256=child_sha256,upstream_sha256=upstream_sha256,role='qwen_floor268',request_received=False)
    need(value==expected and type(value.get('v')) is int and type(value.get('sequence')) is int and value.get('request_received') is False,'exact_ready_identity_required')
    return value
def runtime_args(original_runtime,root,work,*,entry,child,entry_sha256,child_sha256,upstream_sha256,nonce):
    for path,pin in ((Path(entry),entry_sha256),(Path(child),child_sha256)):
        need(not path.is_symlink() and path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()==pin,'reviewed_warm_source_required')
    need(re.fullmatch('[0-9a-f]{32}',nonce) and re.fullmatch('[0-9a-f]{64}',upstream_sha256),'fixed_public_ready_identity_required')
    args=original_runtime(root,work)
    need(args[-5:]==['--','/env/bin/python','-B','-m','onit_candidate.child'] and args[:2]==['bwrap','--unshare-all'] and '--new-session' in args,'original_networkless_native_runtime_required')
    return args[:-5]+['--ro-bind',str(Path(child).resolve()),'/bridge/onit_candidate/child.py','--ro-bind',str(Path(entry).resolve()),'/me3-warm-entry.py','--','/env/bin/python','-B','/me3-warm-entry.py','--nonce',nonce,'--child-sha256',child_sha256,'--upstream-sha256',upstream_sha256]
class PreparedWorker:
    def __init__(self,process,*,created_at,work,stderr_path,nonce,child_sha256,upstream_sha256,source_fingerprint,current_source_fingerprint,current_owner,clock=time.monotonic,signal_process=None):
        need(re.fullmatch('[0-9a-f]{32}',nonce) and all(re.fullmatch('[0-9a-f]{64}',v) for v in (child_sha256,upstream_sha256,source_fingerprint)),'public_worker_pins_required')
        self.process=process;self.work=Path(work);self.stderr_path=Path(stderr_path);self.nonce=nonce;self.child_sha256=child_sha256;self.upstream_sha256=upstream_sha256
        self.fingerprint=source_fingerprint;self.source=current_source_fingerprint;self.owner=current_owner;self.clock=clock;self.signal_process=signal_process or (lambda process,sig:os.killpg(process.pid,sig))
        need(type(created_at) in (int,float) and math.isfinite(created_at) and 0<=created_at<=clock(),'pre_spawn_monotonic_timestamp_required')
        self.state='STARTING';self.created=created_at;self.ready_at=None;self.ready_seconds=None;self.read_task=None;self.wait_task=None;self.adopted=False
    def current(self):return self.owner() is True and self.source()==self.fingerprint and self.process.returncode is None
    async def wait_ready(self):
        need(self.state=='STARTING','ready_once_required')
        self.state='WAITING_READY'
        try:
            need(self.current(),'warm_owner_or_source_changed')
            self.read_task=asyncio.create_task(self.process.stdout.readline())
            done,pending=await asyncio.wait({self.read_task},timeout=max(0,30-(self.clock()-self.created)))
            need(not pending,'warm_ready_deadline')
            ready_frame(self.read_task.result(),nonce=self.nonce,child_sha256=self.child_sha256,upstream_sha256=self.upstream_sha256)
            need(self.current(),'warm_owner_or_source_changed')
            self.ready_at=self.clock();self.ready_seconds=self.ready_at-self.created;self.state='READY'
            return dict(imports_ready=True,ready_seconds=self.ready_seconds,maximum_idle_seconds=60,request_received=False)
        except BaseException:
            await self.close();raise
    async def acquire(self,*,current):
        try:
            need(self.state=='READY' and not self.adopted,'warm_worker_not_ready_or_consumed')
            need(self.current() and current() is True and 0<=self.clock()-self.ready_at<=60,'warm_worker_expired_or_owner_changed')
            self.adopted=True;self.state='ADOPTED';return self.process
        except BaseException:
            if not self.adopted:await self.close()
            raise
    def record_closed(self,process):
        need(self.adopted and process is self.process and process.returncode is not None,'actual_adopted_process_exit_required')
        self.state='CLOSED'
    async def close(self):
        if self.state=='CLOSED':return
        need(not self.adopted,'adopted_process_cleanup_belongs_to_Engine')
        self.state='CLOSING'
        if self.read_task is not None and not self.read_task.done():self.read_task.cancel()
        if self.process.returncode is None:
            try:self.signal_process(self.process,signal.SIGTERM)
            except ProcessLookupError:pass
            self.wait_task=asyncio.create_task(self.process.wait())
            done,pending=await asyncio.wait({self.wait_task},timeout=2)
            if pending:
                try:self.signal_process(self.process,signal.SIGKILL)
                except ProcessLookupError:pass
                done,pending=await asyncio.wait({self.wait_task},timeout=2)
            if pending:self.state='FAILED';raise RuntimeError('warm_process_cleanup_unsettled')
        if self.read_task is not None and not self.read_task.done():
            done,pending=await asyncio.wait({self.read_task},timeout=1)
            if pending:self.state='FAILED';raise RuntimeError('warm_ready_reader_cleanup_unsettled')
        if self.process.stdin is not None:self.process.stdin.close()
        need(self.process.returncode is not None,'actual_process_exit_required');self.state='CLOSED'
