"""Counted host Responses transport. No planner, fallback, output quota or retry.

Engine owns the sole SharedBudget reserve. begin_goal resets private replay only
between goals; bind_reservation hands one exact host ordinal to one request.
"""
import asyncio,hashlib,inspect,json,math,re,time,uuid,weakref
from pathlib import Path
import aiohttp
from . import auth,responses as codec_module
from .http import HTTPS
from .usage import UsageLedger

PROVIDER='openai_chatgpt_plan'
MODEL='gpt-6.1-sol'
REQUEST_TOTAL_SECONDS=180
CONNECT_TIMEOUT_SECONDS=10
STREAM_IDLE_TIMEOUT_SECONDS=45
class TransportError(ValueError):pass

# One process-local ordering fence per actual journal and event loop. The real
# private-file locks/full-prefix validation/append/fsync remain unchanged.
_LEDGER_LOCKS=weakref.WeakKeyDictionary()

def connection_failure_code(error):
    """Exception classes only: never serialize endpoint/token/exception text."""
    if isinstance(error,aiohttp.ConnectionTimeoutError):return 'openai_connect_timeout_no_retry'
    if isinstance(error,aiohttp.SocketTimeoutError):return 'openai_stream_idle_timeout_no_retry'
    if isinstance(error,aiohttp.ServerTimeoutError):return 'openai_server_timeout_no_retry'
    if isinstance(error,TimeoutError):return 'openai_request_total_timeout_no_retry'
    if isinstance(error,aiohttp.ServerDisconnectedError):return 'openai_stream_disconnected_no_retry'
    if isinstance(error,aiohttp.ClientConnectorError):return 'openai_connect_failed_no_retry'
    if isinstance(error,aiohttp.ClientConnectionError):return 'openai_connection_failed_no_retry'
    return None

AUTH_FAILURE_CODES=frozenset({
    'auth_store_busy','private_storage_requires_POSIX',
    'private_storage_unavailable_or_link','private_storage_permissions_or_links',
    'untrusted_storage_ancestor','private_record_unavailable_or_link',
    'private_record_size','private_record_invalid','private_account_binding_invalid',
    'account_not_registered','private_selection_invalid',
    'private_selection_binding_invalid','ChatGPT_plan_access_not_enabled',
    'refresh_required','refresh_unavailable','token_endpoint_failed_no_retry',
    'token_endpoint_rejected_no_retry','returning_account_identity_mismatch',
})

def auth_failure_code(error):
    """Only source-defined codes; no provider text or private record values."""
    if type(error) is auth.AuthError and len(error.args)==1 and type(error.args[0]) is str and error.args[0] in AUTH_FAILURE_CODES:return error.args[0]
    return None
def need(value,code):
    if not value:raise TransportError(code)
def root_path(root=None):return Path(root).resolve() if root is not None else Path(__file__).resolve().parents[1]

class Credentials:
    """Pins the selected local account/model; never exports private identities."""
    def __init__(self,root=None,*,store=None,http=None,clock=time.time):
        self.store=store if store is not None else auth.AuthStore(root_path(root)/'data')
        self.http=http if http is not None else HTTPS();self.clock=clock
        self._worker=None
        self.selection=self.store.selection()
        need(isinstance(self.selection,dict) and self.selection.get('model_slug')==MODEL,'qualified_selected_model_required')
        self.account_sha256=hashlib.sha256(json.dumps({k:self.selection[k] for k in ('client_id','issuer','subject')},sort_keys=True).encode()).hexdigest()
        self.local_status()
    def local_status(self):
        need(self.store.selection()==self.selection,'saved_account_or_model_changed')
        row=next((v for v in self.store.accounts() if v.get('client_id')==self.selection['client_id']),None)
        need(isinstance(row,dict) and row.get('plan_enabled') is True and row.get('issuer')==self.selection['issuer'] and row.get('subject')==self.selection['subject'],'selected_account_unavailable')
        expires=row.get('access_expires_at');need(type(expires) in {int,float} and math.isfinite(expires),'selected_account_expiry_invalid')
        return dict(refresh_required=expires<=self.clock()+60)
    def _token(self):
        status=self.local_status()
        if status['refresh_required']:
            summary=self.store.refresh(self.selection['client_id'],self.http.post,self.http.discovery())
            need(all(summary.get(k)==self.selection[k] for k in ('client_id','issuer','subject')),'refreshed_account_binding_changed')
        self.local_status()
        return self.store.access_token(self.selection['client_id'])
    async def token(self):
        # An interrupted goal must not cancel a running private refresh thread
        # or start a second refresh. A subsequent owner goal may await the same
        # worker, then the transport rechecks its current cancellation signal.
        if self._worker is None:
            self._worker=asyncio.create_task(asyncio.to_thread(self._token))
            self._worker.add_done_callback(lambda task:None if task.cancelled() else task.exception())
        worker=self._worker
        try:return await asyncio.shield(worker)
        finally:
            if worker.done() and self._worker is worker:self._worker=None
    async def close(self):
        worker=self._worker
        if worker is not None:
            try:await asyncio.shield(worker)
            except Exception:pass # Fixed auth failure is handled by the request.
            finally:
                if worker.done() and self._worker is worker:self._worker=None

def selected_profile(root=None):
    Credentials(root)
    return dict(provider=PROVIDER,model=MODEL)

def capacity_snapshot(root=None):
    try:
        value=Credentials(root).local_status()
        return dict(ready=True,reasons=[],**value,provider_capacity_verified=False,network_calls=0,counters_changed=False,reasoning_provider=PROVIDER,reasoning_model=MODEL)
    except (auth.AuthError,TransportError):
        return dict(ready=False,reasons=['ChatGPT account setup or selected model requires attention.'],refresh_required=None,provider_capacity_verified=False,network_calls=0,counters_changed=False,reasoning_provider=PROVIDER,reasoning_model=MODEL)

class Transport:
    provider=PROVIDER
    def __init__(self,emit,*,root=None,model=None,session_id=None,get_token=None,ledger=None,account_sha256=None,session=None,store=None,http=None,validated_final_units=False):
        need(callable(emit),'explicit_event_handler_required')
        need(model in (None,MODEL),'qualified_selected_model_required');self.model=MODEL;self.emit=emit
        self.credentials=None
        if get_token is None:
            self.credentials=Credentials(root,store=store,http=http);get_token=self.credentials.token;account_sha256=self.credentials.account_sha256
        need(callable(get_token) and isinstance(account_sha256,str) and re.fullmatch('[a-f0-9]{64}',account_sha256) is not None,'private_account_binding_required')
        self.get_token=get_token;self.account_sha256=account_sha256
        self.ledger=ledger if ledger is not None else UsageLedger(root_path(root)/'data')
        self.session_id=session_id or 'normal-'+uuid.uuid4().hex
        self.session=session;self.own_session=session is None;self.stop_requested=lambda:False
        self.active=False;self.closed=False;self.failed=False;self.goal_id=None;self._reservation=None;self._last_ordinal=0;self.codec=None
        self.validated_final_units=validated_final_units is True
        self._ledger_jobs=set()
    async def _ledger_call(self,name,*args,**kwargs):
        loop=asyncio.get_running_loop();locks=_LEDGER_LOCKS.setdefault(loop,{})
        directory=getattr(self.ledger,'directory',None)
        key=('path',str(directory)) if directory is not None else ('instance',id(self.ledger))
        lock=locks.setdefault(key,asyncio.Lock())
        async def work():
            async with lock:
                return await asyncio.to_thread(getattr(self.ledger,name),*args,**kwargs)
        job=asyncio.create_task(work());self._ledger_jobs.add(job);cancelled=False
        try:
            while not job.done():
                try:await asyncio.shield(job)
                except asyncio.CancelledError:cancelled=True
            return job.result(),cancelled
        finally:self._ledger_jobs.discard(job)
    async def _settle_ledger_jobs(self):
        cancelled=False
        for job in tuple(self._ledger_jobs):
            while not job.done():
                try:await asyncio.shield(job)
                except asyncio.CancelledError:cancelled=True
            job.result()
        return cancelled
    def stopped(self):
        check=self.stop_requested if callable(self.stop_requested) else getattr(self.stop_requested,'is_set',None)
        need(callable(check),'cancellation_signal_required');value=check();need(type(value) is bool,'boolean_cancellation_signal_required');return value
    def begin_goal(self,goal_id):
        need(not self.closed and not self.active and not self.failed and self._reservation is None,'transport_not_ready_for_goal')
        need(isinstance(goal_id,str) and re.fullmatch('[A-Za-z0-9_.:-]{1,128}',goal_id) is not None,'host_goal_identity_required')
        self.goal_id=goal_id;self.codec=codec_module.ResponsesCodec(self.model,allow_no_server_output_cap=True,validated_final_units=self.validated_final_units)
    def bind_reservation(self,ordinal):
        need(not self.closed and not self.active and not self.failed and self.goal_id is not None and self._reservation is None,'one_host_reservation_required')
        need(type(ordinal) is int and ordinal>self._last_ordinal,'monotonic_shared_reasoning_ordinal_required')
        self._last_ordinal=ordinal;self._reservation=ordinal
    def end_goal(self):
        need(not self.active,'goal_cleanup_before_request_settled')
        if self.codec is not None:self.codec.abort()
        self.codec=None;self.goal_id=None;self._reservation=None
    async def request(self,role,*,capacity_wait_seconds=60,json=None,on_final_unit=None):
        need(not self.closed and not self.active and not self.failed and role=='reasoning' and self.codec is not None,'transport_not_ready')
        if self.stopped():raise asyncio.CancelledError()
        unit_format=isinstance(json,dict) and codec_module._same_typed_json(json.get('response_format'),codec_module.unit_response_format())
        if on_final_unit is not None or unit_format:
            need(self.validated_final_units and unit_format and callable(on_final_unit),'validated_final_units_explicit_request_capability_required')
        ordinal=self._reservation;need(type(ordinal) is int and ordinal>0,'exact_shared_reasoning_reservation_required');self._reservation=None
        wire=self.codec.encode_request(json)
        ticket=None
        self.active=True;started=time.monotonic();started_ns=time.monotonic_ns();terminal=False;dispatched=False
        goal_id=self.goal_id
        async def observed_final_unit(observation):
            # Provider identities came through the strict SSE parser; host goal
            # and reserved ordinal come only from this request's local closure.
            # Engine/child still own admission, current generations and commits.
            if self.stopped():raise asyncio.CancelledError()
            need(not self.closed and self.active and self.goal_id==goal_id,
                 'validated_final_units_stale_host_request')
            accepted=on_final_unit({**observation,'goal_id':goal_id,'reservation_ordinal':ordinal})
            need(inspect.isawaitable(accepted),'validated_final_units_async_admission_required')
            accepted=await accepted
            if self.stopped():raise asyncio.CancelledError()
            need(not self.closed and self.active and self.goal_id==goal_id,
                 'validated_final_units_stale_host_request')
            return accepted
        def stage(name):
            # Local closure binds this request's ordinal, never mutable codec or
            # prior-generation state. Text receipt is not speech or authority.
            now=time.monotonic_ns()
            self.emit('openai_plan_stage_timing',ordinal=ordinal,stage=name,monotonic_ns=now,elapsed_seconds=round((now-started_ns)/1e9,6))
        try:
            ticket,reservation_cancelled=await self._ledger_call('reserve',ordinal,self.model,session_id=self.session_id,account_sha256=self.account_sha256)
            if reservation_cancelled:raise asyncio.CancelledError()
            token=self.get_token()
            if inspect.isawaitable(token):token=await token
            need(isinstance(token,str) and token and all(33<=ord(c)<=126 for c in token),'private_access_token_required')
            if self.stopped():raise asyncio.CancelledError()
            stage('auth_ready')
            if self.session is None:self.session=aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=REQUEST_TOTAL_SECONDS,connect=CONNECT_TIMEOUT_SECONDS,sock_read=STREAM_IDLE_TIMEOUT_SECONDS),trust_env=False)
            _,dispatch_cancelled=await self._ledger_call('dispatch',ticket);dispatched=True
            if dispatch_cancelled:raise asyncio.CancelledError()
            self.emit('openai_plan_request_dispatch',ordinal=ordinal,model=self.model,server_output_token_cap=False)
            stage('dispatch')
            async with self.session.post(codec_module.ENDPOINT,json=wire,headers={'Authorization':'Bearer '+token,'Accept':'text/event-stream'},allow_redirects=False) as response:
                stage('response_headers')
                need(response.status==200,'openai_HTTP_'+str(response.status)+'_no_retry')
                kind={'text/event-stream':'event_stream','application/json':'json','text/html':'html','text/plain':'plain','application/octet-stream':'octet_stream'}.get(response.content_type,'other')
                self.emit('openai_plan_response_metadata',status=response.status,content_type_class=kind)
                async def chunks():
                    first=True
                    async for chunk in response.content.iter_chunked(8192):
                        if first and chunk:
                            head=chunk[:128].lstrip();shape='sse' if head.startswith((b'event:',b'data:',b':')) else 'json' if head.startswith((b'{',b'[')) else 'html' if head.startswith(b'<') else 'other'
                            self.emit('openai_plan_response_prefix',shape=shape);first=False
                        yield chunk
                extra={}
                if on_final_unit is not None:
                    extra=dict(final_units=codec_module.FinalUnitParser(enabled=True),on_final_unit=observed_final_unit)
                completed=await codec_module.read_sse(chunks(),response.close,stop_requested=self.stopped,on_event=lambda name:self.emit('openai_plan_stream_event_type',type=name),on_diagnostic=lambda value:self.emit('openai_plan_stream_completion_shape',**value),on_stage=stage,finalized_stream_output=True,**extra)
            value=self.codec.decode_completed(completed)
            stage('decode_finished')
            _,completion_cancelled=await self._ledger_call('complete',ticket,usage=completed['usage'],response_id=completed['id']);terminal=True
            if completion_cancelled:raise asyncio.CancelledError()
            self.emit('openai_plan_request_completed',ordinal=ordinal,model=self.model,elapsed_seconds=round(time.monotonic()-started,3),usage=value['usage'],server_output_token_cap=False)
            return value
        except BaseException as exc:
            self.codec.abort();cancelled=isinstance(exc,asyncio.CancelledError);self.failed=not cancelled
            auth_code=auth_failure_code(exc)
            connection_code=connection_failure_code(exc)
            settlement_cancelled=False
            if ticket is not None and not terminal:
                _,settlement_cancelled=await self._ledger_call('interrupt' if cancelled else 'fail',ticket,'cancelled' if cancelled else 'transport_error')
            self.emit('openai_plan_request_failed',ordinal=ordinal,model=self.model,dispatched=dispatched,elapsed_seconds=round(time.monotonic()-started,3),error_code='request_interrupted' if cancelled else auth_code or connection_code or 'request_failed_no_retry')
            if cancelled:raise
            if settlement_cancelled:raise asyncio.CancelledError() from exc
            if auth_code is not None:raise TransportError(auth_code) from None
            if connection_code is not None:raise TransportError(connection_code) from None
            if isinstance(exc,(TransportError,codec_module.ResponsesError)):raise
            raise TransportError('openai_connection_failed_no_retry') from None
        finally:self.active=False
    async def close(self):
        self.closed=True
        ledger_cancelled=False
        try:
            ledger_cancelled=await self._settle_ledger_jobs()
        finally:
            try:
                if self.credentials is not None:await self.credentials.close()
            finally:
                if self.session is not None and self.own_session:await self.session.close()
        if ledger_cancelled:raise asyncio.CancelledError()
