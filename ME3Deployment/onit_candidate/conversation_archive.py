"""Private text-only archive before explicit New conversation. No restoration."""
import json,math,os,re,stat,time,uuid
from datetime import datetime,timezone
from pathlib import Path
MAX_BYTES=1024*1024

def archive(root,snapshot,*,now=None):
    """Durably archive exact displayed text, or fail without clearing memory."""
    if not isinstance(snapshot,dict) or snapshot.get('active') is not False or snapshot.get('call_held') is not False:raise ValueError('stop_before_new_conversation')
    cid=snapshot.get('conversation_id');source=snapshot.get('source_revision');messages=snapshot.get('messages')
    if not isinstance(cid,str) or re.fullmatch('[0-9a-f]{32}',cid) is None or not isinstance(source,str) or re.fullmatch('[0-9a-f]{64}',source) is None or not isinstance(messages,list):raise ValueError('conversation_archive_identity_invalid')
    rows=[]
    for row in messages:
        if not isinstance(row,dict) or row.get('role') not in {'user','assistant'} or not isinstance(row.get('text'),str):raise ValueError('conversation_archive_text_invalid')
        item=dict(role=row['role'],text=row['text'])
        if row.get('state') in {'preparing','interrupted','failed'}:item['state']=row['state']
        rows.append(item)
    if not rows:return dict(archived=False,messages=0,path=None)
    now=time.time() if now is None else now
    if type(now) not in {float,int} or not math.isfinite(now) or now<0:raise ValueError('conversation_archive_clock_invalid')
    value=dict(version=1,scope='displayed_conversation_text_only_no_action_authority',conversation_id=cid,source_revision=source,archived_at=datetime.fromtimestamp(now,timezone.utc).isoformat(),messages=rows)
    raw=json.dumps(value,ensure_ascii=False,allow_nan=False,indent=2).encode('utf-8')+b'\n'
    if len(raw)>MAX_BYTES:raise ValueError('conversation_archive_too_large_nothing_cleared')
    root=Path(root).absolute();data=root/'data';folder=data/'conversation-archives'
    if root!=root.resolve() or not data.is_dir() or data.is_symlink():raise ValueError('private_conversation_archive_directory_required')
    if os.name!='nt' and any(p.stat().st_uid!=os.getuid() for p in (root,data)):raise ValueError('private_conversation_archive_owner_required')
    created=False
    try:folder.mkdir(mode=0o700);created=True
    except FileExistsError:pass
    if folder.is_symlink() or not folder.is_dir():raise ValueError('private_conversation_archive_directory_required')
    info=folder.stat()
    if os.name!='nt' and (info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o700):raise ValueError('private_conversation_archive_owner_required')
    if created and os.name!='nt':
        parent=os.open(data,os.O_RDONLY|getattr(os,'O_DIRECTORY',0))
        try:os.fsync(parent)
        finally:os.close(parent)
    path=folder/(cid+'-'+uuid.uuid4().hex+'.json')
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
    with os.fdopen(fd,'wb') as handle:handle.write(raw);handle.flush();os.fsync(handle.fileno())
    if os.name!='nt':
        directory=os.open(folder,os.O_RDONLY|getattr(os,'O_DIRECTORY',0))
        try:os.fsync(directory)
        finally:os.close(directory)
    return dict(archived=True,messages=len(rows),path=path.relative_to(root).as_posix())
