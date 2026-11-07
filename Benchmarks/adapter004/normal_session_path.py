"""Evidence allocation only; never grants source, model or action authority."""
import hashlib,json,os,stat
from pathlib import Path

def need(value,code):
 if not value:raise ValueError(code)

def plain_directory_chain(path,root):
 need(path.is_absolute() and '..' not in path.parts and root==root.resolve() and path.is_relative_to(root),'normal_evidence_root_containment_required')
 for item in (path,*path.parents):
  need(not item.is_symlink(),'normal_evidence_symlink_refused')
  if item.exists():need(item.is_dir(),'normal_evidence_directory_required')
  if item==root:break

def allocate(root,trial,contract_sha256,ordinal,source_revision):
 root=Path(root).absolute();trial=Path(trial).absolute()
 need(type(contract_sha256) is str and len(contract_sha256)==64 and all(c in '0123456789abcdef' for c in contract_sha256),'session_contract_digest_required')
 need(type(ordinal) is int and 1<=ordinal<=450,'bounded_session_ordinal_required')
 parent=root/'evidence/assistant/sessions'
 plain_directory_chain(parent,root);plain_directory_chain(trial,root)
 need(parent.is_dir(),'existing_normal_sessions_root_required')
 trial.mkdir(parents=True,exist_ok=True)
 session=parent/('b-'+contract_sha256[:16]+'-%03d'%ordinal)
 # The actual normal EventLog owns exclusive creation of the final directory.
 # Claim separately; never precreate/reuse/delete its eventual session path.
 need(not session.is_symlink(),'normal_session_symlink_refused')
 if session.exists():raise FileExistsError('normal_session_already_exists')
 claims=root/'evidence/assistant/benchmark-session-claims'
 plain_directory_chain(claims,root)
 try:claims.mkdir(mode=0o700)
 except FileExistsError:pass
 info=claims.lstat();need(stat.S_ISDIR(info.st_mode) and not claims.is_symlink(),'private_session_claim_directory_required')
 if os.name=='posix':need(info.st_uid==os.geteuid() and stat.S_IMODE(info.st_mode)==0o700,'private_session_claim_directory_required')
 claim=claims/(session.name+'.json')
 value=dict(version=1,contract_sha256=contract_sha256,execution_ordinal=ordinal,source_revision=source_revision,session_relative_path=session.relative_to(root).as_posix(),claim_relative_path=claim.relative_to(root).as_posix())
 pointer=trial/('normal-session-%03d.json'%ordinal)
 raw=(json.dumps(value,sort_keys=True,indent=2)+'\n').encode()
 fd=os.open(claim,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
 with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
 fd=os.open(pointer,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
 with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
 return session,pointer,hashlib.sha256(raw).hexdigest()

def allocate_intent_ledger(root,contract_sha256,ordinal):
 root=Path(root).resolve();parent=root/'.tmp';plain_directory_chain(parent,root)
 need(parent.is_dir() and type(ordinal) is int and 1<=ordinal<=450,'private_benchmark_ledger_parent_required')
 need(type(contract_sha256) is str and len(contract_sha256)==64 and all(c in '0123456789abcdef' for c in contract_sha256),'private_benchmark_contract_required')
 folder=parent/('benchmark441-'+contract_sha256[:16]+'-%03d'%ordinal)
 folder.mkdir(mode=0o700) # Exclusive: never opens/reuses an existing ledger.
 return folder/'intent-ledger.jsonl'
