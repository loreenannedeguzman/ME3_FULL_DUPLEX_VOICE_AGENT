"""Pi-owned artifact workspace. No arbitrary project, home or secret access."""
import hashlib,json,os,re,stat,uuid
from contextlib import contextmanager
from pathlib import Path

MAX_FILES=128
MAX_TOTAL=1048576
MAX_FILE=65536
MAX_WRITE=MAX_FILE
EXTENSIONS={'.py','.html','.css','.js','.json','.txt','.md','.csv','.tsv','.svg','.toml','.cfg','.ini'}
class WorkspaceError(ValueError):pass
WRITE_REJECTIONS=frozenset({'invalid_workspace_path','unsupported_workspace_extension','workspace_write_size_or_encoding','invalid_workspace_version'})
WRITE_GUIDANCE='No artifact was written and no filesystem operation started. Choose a valid relative supported path, UTF-8 text without NUL of at most65536bytes, and NEW or a syntactically valid current SHA256. Discover the current version before replacing an existing file. You may choose corrected arguments within the remaining task budget; never repeat the same rejected call or retry an executed, failed or uncertain write.'
class WriteParameterError(WorkspaceError):
    def __init__(self,reason):
        if reason not in WRITE_REJECTIONS:raise WorkspaceError('invalid_write_parameter_reason')
        self.reason=reason;super().__init__(reason)

def write_rejection(reason):
    if reason not in WRITE_REJECTIONS:raise WorkspaceError('invalid_write_parameter_reason')
    return dict(ok=False,status='parameter_rejected',error='workspace_write_parameters_rejected',rejection_kind='write_parameters',rejection_reason=reason,no_effect=True,execution_started=False,automatic_retry=False,guidance=WRITE_GUIDANCE)

def validate_write_rejection(value):
    if not isinstance(value,dict) or type(value.get('rejection_reason')) is not str or value['rejection_reason'] not in WRITE_REJECTIONS:raise WorkspaceError('invalid_write_parameter_rejection')
    expected=write_rejection(value['rejection_reason'])
    if value!=expected or any(type(value.get(k)) is not type(v) for k,v in expected.items()):raise WorkspaceError('invalid_write_parameter_rejection')
    return value['rejection_reason']

def validate_write_parameters(path,text,expected_sha256='NEW'):
    """Pure syntax/encoding validation, before folder creation or any I/O."""
    try:parts=relative(path)
    except WorkspaceError as exc:
        if str(exc) not in {'invalid_workspace_path','unsupported_workspace_extension'}:raise
        raise WriteParameterError(str(exc)) from None
    try:raw=text.encode('utf-8') if isinstance(text,str) and '\x00' not in text else None
    except UnicodeError:raw=None
    if raw is None or len(raw)>MAX_WRITE:raise WriteParameterError('workspace_write_size_or_encoding')
    if expected_sha256!='NEW' and (not isinstance(expected_sha256,str) or not re.fullmatch('[0-9a-f]{64}',expected_sha256)):raise WriteParameterError('invalid_workspace_version')
    return parts,raw

def digest(raw):return hashlib.sha256(raw).hexdigest()

def relative(value,*,directory=False):
    if directory and value=='':return []
    if not isinstance(value,str) or len(value)>240:raise WorkspaceError('invalid_workspace_path')
    parts=value.split('/')
    if '__pycache__' in parts:raise WorkspaceError('invalid_workspace_path')
    if not 1<=len(parts)<=6 or any(not re.fullmatch(r'[A-Za-z0-9_\-][A-Za-z0-9_.\-]{0,63}',p) or '..' in p or p.endswith('.') or p.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*[f'COM{i}' for i in range(1,10)],*[f'LPT{i}' for i in range(1,10)]} for p in parts):raise WorkspaceError('invalid_workspace_path')
    if not directory and Path(parts[-1]).suffix.lower() not in EXTENSIONS:raise WorkspaceError('unsupported_workspace_extension')
    return parts

def plain(info):return stat.S_ISREG(info.st_mode) and info.st_nlink==1

class Workspace:
    def __init__(self,root):
        self.root=Path(root).absolute()
        # All operations use directory descriptors and O_NOFOLLOW on the target Pi.
        # Other platforms fail closed; no weaker Windows file-write fallback.
    def supported(self):return os.name=='posix' and os.open in os.supports_dir_fd
    @contextmanager
    def folder(self,parts=(),create=False):
        if not self.supported():raise WorkspaceError('workspace_requires_posix_directory_handles')
        fd=None
        try:
            fd=os.open(self.root.anchor,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            # Resolve each ancestor through an opened directory, never through links.
            for part in [*self.root.parts[1:],*parts]:
                if create:
                    try:os.mkdir(part,mode=0o700,dir_fd=fd)
                    except FileExistsError:pass
                nxt=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
                os.close(fd);fd=nxt
            yield fd
        except WorkspaceError:raise
        except OSError:raise WorkspaceError('workspace_path_unavailable_or_link') from None
        finally:
            if fd is not None:os.close(fd)
    def _read(self,fd,name):
        before=os.stat(name,dir_fd=fd,follow_symlinks=False)
        if not plain(before) or before.st_size>MAX_FILE:raise WorkspaceError('workspace_file_type_or_size')
        handle=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
        with os.fdopen(handle,'rb') as f:
            opened=os.fstat(f.fileno())
            if not plain(opened) or (before.st_dev,before.st_ino)!=(opened.st_dev,opened.st_ino):raise WorkspaceError('workspace_file_changed')
            raw=f.read(MAX_FILE+1);after=os.fstat(f.fileno())
        current=os.stat(name,dir_fd=fd,follow_symlinks=False)
        if len(raw)>MAX_FILE or (opened.st_dev,opened.st_ino,opened.st_mtime_ns,opened.st_size)!=(current.st_dev,current.st_ino,current.st_mtime_ns,current.st_size) or (opened.st_mtime_ns,opened.st_size)!=(after.st_mtime_ns,after.st_size):raise WorkspaceError('workspace_file_changed')
        return raw
    def files(self):
        result={}
        def walk(fd,prefix='',depth=0):
            entries=os.listdir(fd)
            if len(entries)>MAX_FILES:raise WorkspaceError('workspace_file_count_limit')
            for name in sorted(entries):
                # Internal revision records are never in the model catalog or sandbox.
                if not prefix and name=='.versions':continue
                path=prefix+name;info=os.stat(name,dir_fd=fd,follow_symlinks=False)
                # Generated Python caches are neither artifacts nor source
                # snapshots. Inspect the entry itself, never follow or walk it.
                if name=='__pycache__':
                    if not stat.S_ISDIR(info.st_mode):raise WorkspaceError('workspace_cache_not_directory')
                    continue
                if stat.S_ISDIR(info.st_mode):
                    relative(path,directory=True)
                    if depth>=5:raise WorkspaceError('workspace_depth_limit')
                    sub=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
                    try:walk(sub,path+'/',depth+1)
                    finally:os.close(sub)
                else:
                    relative(path);result[path]=self._read(fd,name)
                    if len(result)>MAX_FILES or sum(map(len,result.values()))>MAX_TOTAL:raise WorkspaceError('workspace_capacity_limit')
        if not self.root.exists():return result
        with self.folder() as fd:walk(fd)
        return result
    def listing(self,offset=0):
        if type(offset) is not int or not 0<=offset<=MAX_FILES:raise WorkspaceError('invalid_workspace_offset')
        files=self.files();names=sorted(files);page=names[offset:offset+12]
        return {'ok':True,'files':[{'path':p,'bytes':len(files[p]),'sha256':digest(files[p])} for p in page],'next_offset':offset+12 if offset+12<len(names) else None,'total_files':len(names),'total_bytes':sum(map(len,files.values())),'scope':'dedicated_artifact_workspace','content_is_untrusted_data':True}
    def read(self,path,offset=0,expected_sha256=None):
        parts=relative(path)
        if type(offset) is not int or not 0<=offset<=MAX_FILE:raise WorkspaceError('invalid_workspace_offset')
        if offset and expected_sha256 is None:raise WorkspaceError('workspace_version_required')
        with self.folder(parts[:-1]) as fd:raw=self._read(fd,parts[-1])
        version=digest(raw)
        if expected_sha256 is not None and expected_sha256!=version:raise WorkspaceError('workspace_version_changed')
        try:text=raw.decode('utf-8')
        except UnicodeError:raise WorkspaceError('workspace_file_not_utf8') from None
        if offset>len(text):raise WorkspaceError('invalid_workspace_offset')
        end=min(len(text),offset+1000)
        return {'ok':True,'path':path,'sha256':version,'text':text[offset:end],'next_offset':end if end<len(text) else None,'total_characters':len(text),'content_is_untrusted_data':True}
    def write(self,path,text,expected_sha256='NEW'):
        parts,raw=validate_write_parameters(path,text,expected_sha256)
        with self.folder(create=True) as rootfd:
            import fcntl
            try:fcntl.flock(rootfd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError:raise WorkspaceError('workspace_busy') from None
            try:
                files=self.files();old=files.get(path)
                if expected_sha256=='NEW' and old is not None:raise WorkspaceError('workspace_file_already_exists')
                if expected_sha256!='NEW' and (old is None or digest(old)!=expected_sha256):raise WorkspaceError('workspace_version_changed')
                if len(files)+(old is None)>MAX_FILES or sum(map(len,files.values()))-len(old or b'')+len(raw)>MAX_TOTAL:raise WorkspaceError('workspace_capacity_limit')
                if old is not None:
                    # Preserve the replaced bytes once per content hash; never overwrite history.
                    with self.folder(['.versions'],create=True) as versions:
                        entries=os.listdir(versions)
                        if len(entries)>=256:raise WorkspaceError('workspace_history_limit')
                        name=digest(old)
                        try:
                            handle=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=versions)
                        except FileExistsError:pass
                        else:
                            with os.fdopen(handle,'wb') as f:f.write(old);f.flush();os.fsync(f.fileno())
                with self.folder(parts[:-1],create=True) as fd:
                    temp='.write-'+uuid.uuid4().hex
                    handle=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
                    try:
                        with os.fdopen(handle,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
                        os.replace(temp,parts[-1],src_dir_fd=fd,dst_dir_fd=fd);os.fsync(fd)
                    finally:
                        try:os.unlink(temp,dir_fd=fd)
                        except FileNotFoundError:pass
                    checked=self._read(fd,parts[-1])
                if checked!=raw:raise WorkspaceError('workspace_write_unverified')
                return {'ok':True,'path':path,'sha256':digest(checked),'bytes':len(checked),'operation':'created' if old is None else 'replaced','previous_sha256':digest(old) if old is not None else None,'verified_by_fresh_read':True,'scope':'dedicated_artifact_workspace'}
            finally:fcntl.flock(rootfd,fcntl.LOCK_UN)
