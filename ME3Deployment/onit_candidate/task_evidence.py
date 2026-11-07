"""Private immutable task metadata; artifact versions are observations, not tests.

Only the existing dedicated Workspace reader sees artifact bytes. Historical
receipts confer no confirmation, tool or effect authority and never run code.
"""
import hashlib,json,os,secrets,stat
from pathlib import Path
from onit_candidate.task_checkpoints import ident,need,_private,_syncdir,evidence_refs

class TaskEvidence:
    def __init__(self,root,source_revision,*,workspace=None):
        self.root=Path(root).absolute();self.source=source_revision
        need(ident(source_revision,64),'task_evidence_source')
        from me3_assistant.workspace import Workspace
        self.workspace=workspace or Workspace(self.root/'data/agent-workspace')
        self.folder=self.root/'evidence/assistant/task-checkpoints'
    def versions(self,artifacts):
        need(type(artifacts) is list and len(artifacts)<=16,'bounded_task_artifacts_required')
        from me3_assistant.workspace import relative
        rows=[];seen=set()
        for ref in artifacts:
            need(type(ref) is dict and set(ref)=={'path','sha256'},'task_artifact_shape')
            relative(ref['path']);need(ref['path'] not in seen and ident(ref['sha256'],64),'task_artifact_identity')
            value=self.workspace.read(ref['path'],expected_sha256=ref['sha256'])
            need(value.get('ok') is True and value.get('path')==ref['path'] and value.get('sha256')==ref['sha256'],'task_artifact_current_version_required')
            seen.add(ref['path']);rows.append(dict(ref))
        return rows
    def capture(self,goal,state,artifacts):
        need(ident(goal,32),'task_evidence_goal')
        rows=self.versions(artifacts)
        # Check every ancestor; create only this fixed private evidence leaf.
        need(self.root==self.root.resolve(),'task_evidence_root')
        for part in (self.root/'evidence',self.root/'evidence/assistant'):
            need(part.is_dir() and not part.is_symlink(),'task_evidence_parent')
            if os.name!='nt':need(part.stat().st_uid==os.getuid(),'task_evidence_owner')
        try:self.folder.mkdir(mode=0o700)
        except FileExistsError:pass
        _private(self.folder,directory=True)
        value=dict(version=1,source_revision=self.source,goal_id=goal,task_state=state,
                   artifacts=rows,scope='observed_artifact_versions_not_execution_or_action_success',
                   confirmation_authority=False,automatic_replay=False)
        raw=json.dumps(value,sort_keys=True,separators=(',',':')).encode()
        path=self.folder/(goal+'-'+secrets.token_hex(16)+'.json')
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
        with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
        _syncdir(self.folder)
        return dict(path=path.relative_to(self.root).as_posix(),sha256=hashlib.sha256(raw).hexdigest())
    def reconcile(self,goal,refs,*,state=None):
        refs=evidence_refs(refs);need(len(refs)==1,'one_current_task_evidence_receipt_required')
        ref=refs[0];path=self.root/ref['path']
        need(path.parent==self.folder and path.name.startswith(goal+'-') and path.suffix=='.json','task_evidence_exact_scope')
        need(self.root==self.root.resolve() and self.folder==self.folder.resolve(),'task_evidence_root')
        _private(self.folder,directory=True);st=_private(path)
        need(st.st_size<=12000,'task_evidence_size')
        fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
        with os.fdopen(fd,'rb') as stream:
            opened=os.fstat(stream.fileno());need((st.st_dev,st.st_ino)==(opened.st_dev,opened.st_ino),'task_evidence_changed')
            raw=stream.read(12001);after=os.fstat(stream.fileno())
        current=path.lstat()
        need(len(raw)<=12000 and (opened.st_dev,opened.st_ino,opened.st_size,opened.st_mtime_ns)==(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns)==(current.st_dev,current.st_ino,current.st_size,current.st_mtime_ns) and hashlib.sha256(raw).hexdigest()==ref['sha256'],'task_evidence_changed')
        def pairs(items):
            result={}
            for key,value in items:need(key not in result,'task_evidence_duplicate_key');result[key]=value
            return result
        value=json.loads(raw,object_pairs_hook=pairs)
        need(set(value)=={'version','source_revision','goal_id','task_state','artifacts','scope','confirmation_authority','automatic_replay'} and type(value['version']) is int and value['version']==1 and value['source_revision']==self.source and value['goal_id']==goal and value['scope']=='observed_artifact_versions_not_execution_or_action_success' and value['confirmation_authority'] is False and value['automatic_replay'] is False,'task_evidence_binding')
        need(state is None or value['task_state']==state,'task_evidence_state_binding')
        rows=self.versions(value['artifacts'])
        return dict(artifacts=rows,current_versions_verified=True,execution_success_verified=False,
                    confirmation_authority=False,automatic_execution=False)
