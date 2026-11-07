"""Strict host mapping to the corrected actual424 passive queue. Inactive.

Metadata validation here never replaces asynchronous live artifact/namespace
verification in BackgroundDelivery before speech. No fallback API, launch, jobs,
TTS, action authority or new model request is provided by this adapter.
"""
import hashlib,inspect,re,time
from pathlib import Path
from onit_candidate.background_notices import BackgroundNotices,CompletionNotice,NoticeClaim

CORE_SHA256='e38d82945acfeaac984e615dc536a20a0cb7671434dbc683da6a24001cbe2f04'
READY='Your app is running and ready to view in Artifacts.'
OFFER='Would you like me to open it?'
OUTCOMES={'software_completed':'played','interrupted':'dropped','canceled_before_or_during_notice':'dropped','unverified':'failed'}
def need(value,code):
 if not value:raise ValueError(code)
def identity(value,size):return type(value) is str and re.fullmatch('[0-9a-f]{%d}'%size,value) is not None
class BackgroundCoreAdapter:
 def __init__(self,conversation_id,source_revision):
  need(identity(conversation_id,32) and identity(source_revision,64),'exact_background_source_conversation_required')
  path=Path(inspect.getfile(BackgroundNotices)).resolve()
  need(path==Path(__file__).resolve().with_name('background_notices.py') and hashlib.sha256(path.read_bytes()).hexdigest()==CORE_SHA256,'exact_corrected_actual424_core_required')
  self.conversation_id=conversation_id;self.source_revision=source_revision;self._issuer=object();self._core=BackgroundNotices(self._issuer,capacity=4)
  self._records={};self._claims={};self._bound=False;self._closed=False
 def bind_host(self,*,current,idle,binding):
  need(not self._bound and all(callable(f) for f in (current,idle,binding)),'one_explicit_host_binding_required')
  self._current=current;self._idle=idle;self._binding=binding;self._bound=True
 def enqueue(self,record):
  need(self._bound and type(record) is dict,'host_bound_record_required')
  need(record.get('kind')=='service_ready' and record.get('verified') is True and record.get('ready_offer_allowed') is True and record.get('action_authority') is False,'verified_non_authoritative_READY_required')
  need(record.get('conversation_id')==self.conversation_id and record.get('source_revision')==self.source_revision and record.get('task_id')==record.get('service_id') and identity(record.get('task_id'),32) and identity(record.get('notice_id'),32),'exact_notice_service_session_required')
  need(type(record.get('task_revision')) is int and record['task_revision']>0 and type(record.get('origin_generation')) is int and record['origin_generation']>=0,'exact_origin_provenance_required')
  need(record.get('text')==READY+' '+OFFER and identity(record.get('owner_sha256'),64),'host_verified_ready_text_and_owner_required')
  notice=CompletionNotice(notice_id=record['notice_id'],task_id=record['task_id'],task_revision=record['task_revision'],conversation_id=self.conversation_id,source_revision=self.source_revision,artifact_path=record['path'],artifact_sha256=record['file_sha256'],workspace_sha256=record['workspace_manifest_sha256'],completed_at=time.monotonic(),expires=record['expires_monotonic'],ready_text=READY,offer_text=OFFER)
  if self._closed or self._current() is not True:return False
  accepted=self._core.enqueue(notice,self._issuer)
  if accepted is True:self._records[notice.notice_id]=notice
  return accepted
 def _valid_binding(self,b):
  return (self._bound and not self._closed and type(b) is dict and b.get('conversation_id')==self.conversation_id and b.get('source_revision')==self.source_revision
   and all(type(b.get(k)) is int and b[k]>=0 for k in ('owner_generation','processor_token','output_epoch','context_revision','engine_generation'))
   and self._current() is True and self._binding()==b)
 def claim(self,binding):
  if not self._valid_binding(binding):return None
  def current_notice(notice):
   return type(notice) is CompletionNotice and self._records.get(notice.notice_id)==notice and self._valid_binding(binding)
  claim=self._core.claim(conversation_id=self.conversation_id,source_revision=self.source_revision,now=time.monotonic(),validate=current_notice,delivery_epoch=binding['output_epoch'],controller_generation=binding['owner_generation'],engine_generation=binding['engine_generation'],idle=self._idle() is True,session_active=self._current() is True)
  if claim is None:return None
  need(type(claim) is NoticeClaim and type(claim.notice) is CompletionNotice and current_notice(claim.notice),'actual_frozen_core_claim_required')
  wrapper=dict(notice_id=claim.notice.notice_id,_actual_core_claim=claim,binding=dict(binding))
  self._claims[claim.notice.notice_id]=wrapper
  return wrapper
 def settle(self,wrapper,outcome):
  need(type(wrapper) is dict and type(outcome) is str and outcome in OUTCOMES,'explicit_core_outcome_mapping_required')
  key=wrapper.get('notice_id');need(self._claims.get(key) is wrapper,'exact_issued_claim_wrapper_required')
  claim=wrapper['_actual_core_claim'];need(type(claim) is NoticeClaim,'actual_core_claim_type_required')
  accepted=self._core.settle(claim,OUTCOMES[outcome]);self._claims.pop(key,None);self._records.pop(key,None)
  return accepted
 def retire_expired(self,*,now):
  # This is host bookkeeping only: actual queue refresh forgets no dedup key,
  # starts nothing, and cannot stop a backend. Retain issued claim identities
  # until their exact owner settles, even when the core expires its inflight.
  need(type(now) in (int,float) and __import__('math').isfinite(now),'finite_retirement_time_required')
  if self._closed:return ()
  self._core.peek(self.conversation_id,self.source_revision,now,lambda notice:self._records.get(notice.notice_id)==notice)
  expired=tuple(key for key,notice in self._records.items() if now>=notice.expires and key not in self._claims)
  for key in expired:self._records.pop(key,None)
  return expired
 def discard(self,task_id):
  need(identity(task_id,32),'exact_discard_service_required');count=self._core.discard(task_id=task_id)
  for key,notice in list(self._records.items()):
   if notice.task_id==task_id:self._records.pop(key,None)
  return count
 def close(self):
  # Issued wrapper identities remain bounded until their owner's terminal
  # settlement; discard/close must not turn a legitimate late cancel into an
  # exception or make a copied/forged claim acceptable.
  self._closed=True;self._core.close();self._records.clear()
