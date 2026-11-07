"""Trusted operator evidence for a consumed uncertain message, never automatic UI verification.

No MCP or model tool writes these receipts. The developer imports an explicit
human observation of one particular operation. Original uncertainty stays in
append-only history; this state prohibits replay and claims no automated proof.
"""
import hashlib,json,re
from pathlib import Path
STATE='OWNER_REPORTED_COMPLETE'
RETIRED='RETIRED_UNCERTAIN'
def verify(root,reference,operation_id,action,expected_digest=None):
 root=Path(root).resolve()
 if action!='propose_contact_message' or not isinstance(reference,dict) or set(reference)!={'path','sha256'}:raise ValueError('bound_owner_report_required')
 raw=reference['path']
 if not isinstance(raw,str) or not raw.startswith('evidence/assistant/') or Path(raw).is_absolute() or '..' in Path(raw).parts:raise ValueError('project_owner_report_required')
 p=root/raw
 if not p.resolve().is_relative_to(root/'evidence/assistant') or any(q.is_symlink() for q in [p,*p.parents] if q!=root) or not p.is_file() or p.stat().st_size>12000:raise ValueError('trusted_owner_report_required')
 body=p.read_bytes()
 if hashlib.sha256(body).hexdigest()!=reference['sha256']:raise ValueError('owner_report_changed')
 v=json.loads(body)
 if v.get('status')!=STATE or v.get('source')!='direct_human_observation' or v.get('operation_id')!=operation_id or v.get('action')!=action or v.get('owner_reported_new_copies')!=1 or type(v.get('owner_reported_new_copies')) is not int or v.get('independent_UI_verification') is not False or v.get('automatic_replay') is not False or not re.fullmatch('[0-9a-f]{64}',v.get('expected_digest','')) or expected_digest is not None and v['expected_digest']!=expected_digest:raise ValueError('exact_owner_report_binding_required')
 return v

def verify_correction(root,reference,prior_reference,operation_id,action,expected_digest=None):
 """Developer-only import of a direct human correction; restores uncertainty.

 The old receipt remains immutable and valid as historical evidence. A
 correction never proves non-dispatch, refunds a reservation or permits replay.
 """
 prior=verify(root,prior_reference,operation_id,action,expected_digest)
 root=Path(root).resolve()
 if not isinstance(reference,dict) or set(reference)!={'path','sha256'}:raise ValueError('bound_owner_correction_required')
 raw=reference['path']
 if not isinstance(raw,str) or not raw.startswith('evidence/assistant/') or Path(raw).is_absolute() or '..' in Path(raw).parts:raise ValueError('project_owner_correction_required')
 p=root/raw
 if not p.resolve().is_relative_to(root/'evidence/assistant') or any(q.is_symlink() for q in [p,*p.parents] if q!=root) or not p.is_file() or p.stat().st_size>12000:raise ValueError('trusted_owner_correction_required')
 body=p.read_bytes()
 if hashlib.sha256(body).hexdigest()!=reference['sha256']:raise ValueError('owner_correction_changed')
 v=json.loads(body)
 if v.get('status')!='OWNER_REPORT_RETRACTED' or v.get('source')!='direct_human_correction' or v.get('supersedes')!=prior_reference or v.get('operation_id')!=operation_id or v.get('action')!=action or v.get('expected_digest')!=prior['expected_digest'] or v.get('reported_message_not_sent') is not True or v.get('reported_message_not_received') is not True or v.get('independent_UI_verification') is not False or v.get('automatic_replay') is not False:raise ValueError('exact_owner_correction_binding_required')
 return v

def verify_retirement(root,reference,correction_reference,operation_id,action,expected_digest=None):
 """Retire one unknown attempt without claiming success or allowing replay.

 Only trusted developer import of the subsequent direct owner instruction;
 no MCP/model capability. Future operations need a fresh command and review.
 """
 root=Path(root).resolve()
 def read(ref):
  if not isinstance(ref,dict) or set(ref)!={'path','sha256'}:raise ValueError('bound_retirement_required')
  raw=ref['path']
  if not isinstance(raw,str) or not raw.startswith('evidence/assistant/') or Path(raw).is_absolute() or '..' in Path(raw).parts:raise ValueError('project_retirement_required')
  p=root/raw
  if not p.resolve().is_relative_to(root/'evidence/assistant') or any(q.is_symlink() for q in [p,*p.parents] if q!=root) or not p.is_file() or p.stat().st_size>12000:raise ValueError('trusted_retirement_required')
  body=p.read_bytes()
  if hashlib.sha256(body).hexdigest()!=ref['sha256']:raise ValueError('retirement_changed')
  return json.loads(body)
 c=read(correction_reference);verify_correction(root,correction_reference,c.get('supersedes'),operation_id,action,expected_digest);v=read(reference)
 instruction=v.get('owner_instruction')
 if not isinstance(instruction,str) or not 1<=len(instruction)<=2000 or hashlib.sha256(instruction.encode()).hexdigest()!=v.get('owner_instruction_sha256'):raise ValueError('bound_owner_instruction_required')
 if v.get('status')!=RETIRED or v.get('source')!='direct_owner_instruction' or v.get('operation_id')!=operation_id or v.get('action')!=action or v.get('expected_digest')!=c['expected_digest'] or v.get('supersedes_correction')!=correction_reference or v.get('outcome_remains_unknown') is not True or v.get('independent_UI_verification') is not False or v.get('automatic_replay') is not False or v.get('fresh_command_review_and_confirmation_required') is not True:raise ValueError('exact_retirement_binding_required')
 return v
