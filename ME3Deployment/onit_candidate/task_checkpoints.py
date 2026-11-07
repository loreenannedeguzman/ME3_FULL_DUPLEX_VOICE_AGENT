"""Host-only unfinished-goal references. Never restore execution authority.

Default-off owner primitive: normal GUI callers explicitly select a saved goal
after fresh user intent, reconcile current files/receipts and use the existing
guarded Onit path. No model-facing tool, startup restore, planner or replay.
"""
import copy,hashlib,json,math,os,re,secrets,stat,time
from pathlib import Path,PurePosixPath

MAX_REVISIONS=64
MAX_TASKS=32
MAX_BYTES=12000
STATES={'running','reply_ready','awaiting_owner','waiting_confirmation','interrupted','failed','canceled','completed'}
NEXT={
 'running':{'running','reply_ready','awaiting_owner','waiting_confirmation','interrupted','failed','canceled'},
 'awaiting_owner':{'running','interrupted','failed','canceled','completed'},
 'reply_ready':{'completed','interrupted','failed','canceled'},
 'waiting_confirmation':{'running','interrupted','failed','canceled'},
 'interrupted':{'running','canceled'},'failed':{'running','canceled'},
 'completed':set(),'canceled':set()}
FIELDS={'schema_version','goal_id','conversation_id','source_revision','revision','state','request','request_sha256','evidence','updated_at','previous_sha256'}

def need(test,code):
    if not test:raise ValueError(code)

def encoded(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf-8')
def sha(raw):return hashlib.sha256(raw).hexdigest()
def ident(value,n):return isinstance(value,str) and re.fullmatch('[0-9a-f]{'+str(n)+'}',value) is not None

def evidence_refs(value):
    need(isinstance(value,list) and len(value)<=16,'bounded_task_evidence_required')
    result=[];seen=set()
    for ref in value:
        need(isinstance(ref,dict) and set(ref)=={'path','sha256'},'task_evidence_shape')
        path=ref['path'];digest=ref['sha256']
        need(isinstance(path,str) and 1<=len(path)<=400 and '\\' not in path and ':' not in path and not any(ord(c)<32 for c in path),'task_evidence_path')
        parts=PurePosixPath(path).parts
        need(len(parts)>=3 and parts[:2]==('evidence','assistant') and all(p not in {'.','..',''} for p in path.split('/')) and not path.startswith('/'),'task_evidence_scope')
        need(ident(digest,64) and path not in seen,'task_evidence_identity')
        seen.add(path);result.append(dict(path=path,sha256=digest))
    return result

def validate(row):
    need(isinstance(row,dict) and set(row)==FIELDS,'task_checkpoint_shape')
    need(type(row['schema_version']) is int and row['schema_version']==1,'task_checkpoint_version')
    need(ident(row['goal_id'],32) and ident(row['conversation_id'],32) and ident(row['source_revision'],64),'task_checkpoint_identity')
    need(type(row['revision']) is int and 1<=row['revision']<=MAX_REVISIONS and isinstance(row['state'],str) and row['state'] in STATES,'task_checkpoint_state')
    request=row['request']
    need(isinstance(request,str) and request.strip() and len(request)<=4000 and sha(request.encode('utf-8'))==row['request_sha256'],'task_request_binding')
    evidence_refs(row['evidence'])
    need(type(row['updated_at']) in {int,float} and math.isfinite(row['updated_at']) and row['updated_at']>=0,'task_checkpoint_time')
    need(row['previous_sha256'] is None if row['revision']==1 else ident(row['previous_sha256'],64),'task_checkpoint_chain')
    need(len(encoded(row))<=MAX_BYTES,'task_checkpoint_size')
    return row

def _private(path,*,directory=False):
    need(not path.is_symlink(),'task_checkpoint_symlink')
    st=path.stat();need(stat.S_ISDIR(st.st_mode) if directory else stat.S_ISREG(st.st_mode) and st.st_nlink==1,'task_checkpoint_file_type')
    if os.name!='nt':need(st.st_uid==os.getuid() and stat.S_IMODE(st.st_mode)==(0o700 if directory else 0o600),'task_checkpoint_private_owner')
    return st

def _syncdir(path):
    if os.name=='nt':return
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_DIRECTORY',0))
    try:os.fsync(fd)
    finally:os.close(fd)

class TaskCheckpoints:
    def __init__(self,root,*,clock=time.time):
        self.root=Path(root).absolute();self.folder=self.root/'data'/'task-checkpoints';self.clock=clock
    def directory(self,create=False):
        need(self.root==self.root.resolve() and (self.root/'data').is_dir() and not (self.root/'data').is_symlink(),'task_checkpoint_root')
        if os.name!='nt':need(all(p.stat().st_uid==os.getuid() for p in (self.root,self.root/'data')),'task_checkpoint_root_owner')
        if create:
            try:self.folder.mkdir(mode=0o700)
            except FileExistsError:pass
        if not self.folder.exists():return None
        _private(self.folder,directory=True);return self.folder
    def _rows(self,goal):
        need(ident(goal,32),'task_checkpoint_goal')
        folder=self.directory()
        if folder is None:return []
        paths=sorted(folder.glob(goal+'.*.json'))
        need(len(paths)<=MAX_REVISIONS,'task_checkpoint_revision_bound')
        rows=[];previous=None
        for revision,path in enumerate(paths,1):
            need(path.name==goal+'.'+str(revision).zfill(3)+'.json','task_checkpoint_revision_gap')
            st=_private(path);need(st.st_size<=MAX_BYTES,'task_checkpoint_size')
            raw=path.read_bytes()
            def pairs(items):
                result={}
                for k,v in items:
                    need(k not in result,'task_checkpoint_duplicate_key');result[k]=v
                return result
            row=validate(json.loads(raw,object_pairs_hook=pairs))
            need(row['goal_id']==goal and row['revision']==revision and row['previous_sha256']==previous,'task_checkpoint_chain')
            if rows:
                before=rows[-1][0]
                need(row['conversation_id']==before['conversation_id'] and row['source_revision']==before['source_revision'] and row['state'] in NEXT[before['state']],'task_checkpoint_transition')
            else:need(row['state']=='running','task_checkpoint_initial_state')
            rows.append((row,sha(raw)));previous=sha(raw)
        return rows
    def record(self,*,goal_id,conversation_id,source_revision,request,state,expected_revision,evidence=None):
        """Trusted host data only; exact CAS, immutable history, no effect payloads."""
        need(type(expected_revision) is int and 0<=expected_revision<MAX_REVISIONS,'task_checkpoint_expected_revision')
        need(isinstance(state,str) and state in STATES and (expected_revision or state=='running'),'task_checkpoint_initial_state')
        row=dict(schema_version=1,goal_id=goal_id,conversation_id=conversation_id,source_revision=source_revision,
                 revision=expected_revision+1,state=state,request=request,request_sha256=sha(request.encode('utf-8')) if isinstance(request,str) else '',
                 evidence=evidence_refs([] if evidence is None else evidence),updated_at=self.clock(),previous_sha256=None if expected_revision==0 else '0'*64)
        validate(row) # Reject malformed host inputs before creating storage.
        folder=self.directory(True)
        lock_path=folder/'writer.lock'
        if lock_path.exists() or lock_path.is_symlink():_private(lock_path)
        from me3_assistant.guard import lock
        with lock(lock_path):
            # The existing lock implementation creates with process umask.
            if os.name!='nt':os.chmod(lock_path,0o600)
            if expected_revision==0:need(self.listing()['total']<MAX_TASKS,'task_checkpoint_goal_bound')
            rows=self._rows(goal_id)
            need(len(rows)==expected_revision,'task_checkpoint_revision_conflict')
            if rows:
                old,digest=rows[-1]
                need(old['conversation_id']==conversation_id and old['source_revision']==source_revision and state in NEXT[old['state']],'task_checkpoint_transition')
                need(request==old['request'] or state=='running','task_checkpoint_request_revision_requires_running')
                row['previous_sha256']=digest
            validate(row);raw=encoded(row)
            path=folder/(goal_id+'.'+str(row['revision']).zfill(3)+'.json')
            temporary=folder/('staged-'+secrets.token_hex(16)+'.tmp')
            fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
            with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
            # Only the locked, previously absent next revision is committed.
            # An interrupted write leaves an ignored private .tmp, not a torn
            # latest checkpoint or a changed earlier revision.
            need(not path.exists() and not path.is_symlink(),'task_checkpoint_revision_conflict')
            os.replace(temporary,path)
            _syncdir(folder)
            return dict(goal_id=goal_id,revision=row['revision'],checkpoint_sha256=sha(raw),state=state)
    def reference(self,goal_id):
        """Fresh local read only. Never installs a task/proposal or starts work."""
        rows=self._rows(goal_id)
        if not rows:return None
        row,digest=rows[-1]
        return dict(checkpoint=copy.deepcopy(row),checkpoint_sha256=digest,reference_only=True,
                    content_is_untrusted_reference=True,current_state_verified=False,confirmation_authority=False,
                    automatic_execution=False,automatic_replay=False,
                    policy='Historical host task reference only. Continue only after a fresh current owner request selects this task. Reconcile current files and independent receipts before deciding remaining work. Recorded running/interrupted/failed status is not proof any process is alive. Earlier waiting_confirmation restores NO live proposal: create a new exact review and require separate current confirmation before any effect. Never repeat consumed/uncertain effects; use existing journal readback. A changed request invalidates obsolete planned work, not completed artifacts.')
    def listing(self,offset=0):
        """Explicit authenticated owner read; validate every immutable chain."""
        need(type(offset) is int and 0<=offset<=MAX_TASKS,'bounded_task_list_offset_required')
        folder=self.directory();goals=set();count=0
        if folder is not None:
            with os.scandir(folder) as entries:
                for entry in entries:
                    count+=1;need(count<=MAX_TASKS*MAX_REVISIONS+MAX_REVISIONS+1,'task_checkpoint_storage_bound')
                    name=entry.name
                    if name=='writer.lock' or re.fullmatch('staged-[a-f0-9]{32}\\.tmp',name):
                        _private(Path(entry.path));continue
                    match=re.fullmatch('([a-f0-9]{32})\\.[0-9]{3}\\.json',name)
                    need(match is not None,'unexpected_task_checkpoint_entry')
                    goals.add(match[1]);need(len(goals)<=MAX_TASKS,'task_checkpoint_goal_bound')
        result=[]
        for goal in sorted(goals):
            ref=self.reference(goal);row=ref['checkpoint']
            result.append(dict(goal_id=goal,checkpoint_sha256=ref['checkpoint_sha256'],
                title=row['request'][:160],state=row['state'],revision=row['revision'],
                updated_at=row['updated_at'],source_revision=row['source_revision']))
        result.sort(key=lambda row:(row['updated_at'],row['goal_id']),reverse=True)
        return dict(tasks=result[offset:offset+12],next_offset=offset+12 if offset+12<len(result) else None,
                    total=len(result),reference_only=True,automatic_execution=False)
