"""Host-only, one-use conversation TEXT handover across a GUI source restart.

No effect, proposal, confirmation, retained speech or call authority is imported.
The call key is used only with a separate HMAC domain, never its call verifier.
"""
import hashlib,hmac,json,math,os,re,secrets,stat,time
from pathlib import Path

DOMAIN=b'ME3_GUI_CONVERSATION_TEXT_RESUME_V1\x00'
MAX_BYTES=1024*1024
MAX_MESSAGES=128
FIELDS={'version','source_revision','issued_at','expires_at','nonce','conversation_id','prior_goal_id','messages'}

def _fail():raise ValueError('private_conversation_text_resume_rejected')
def _ident(value):return isinstance(value,str) and re.fullmatch(r'[0-9a-f]{32}',value) is not None
def _json(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')
def _pairs(pairs):
    out={}
    for key,value in pairs:
        if key in out:_fail()
        out[key]=value
    return out
def _syncdir(folder):
    if os.name=='nt':return
    fd=os.open(folder,os.O_RDONLY|getattr(os,'O_DIRECTORY',0))
    try:os.fsync(fd)
    finally:os.close(fd)
def _private(st,mode):
    if st.st_nlink!=1 and stat.S_ISREG(st.st_mode):_fail()
    if os.name!='nt' and (stat.S_IMODE(st.st_mode)!=mode or st.st_uid!=os.getuid()):_fail()
def _directory(root,create=False):
    raw=Path(root).absolute()
    if raw!=raw.resolve():_fail()
    data=raw/'data'
    if data.is_symlink() or not data.is_dir():_fail()
    if os.name!='nt' and any(p.stat().st_uid!=os.getuid() for p in (raw,data)):_fail()
    folder=data/'gui-context-resume'
    if create:
        try:folder.mkdir(mode=0o700)
        except FileExistsError:pass
    if folder.is_symlink() or not folder.is_dir():_fail()
    _private(folder.stat(),0o700)
    return raw,folder
def _key(root):
    from me3_whatsapp.call_service import CallAuthority
    path=root/'data/whatsapp-call-host-key.json'
    if path.is_symlink() or not path.is_file():_fail()
    _private(path.stat(),0o600)
    return CallAuthority(root).key()
def _mac(root,value):return hmac.new(_key(root),DOMAIN+_json(value),hashlib.sha256).hexdigest()
def _validate(value,source,now):
    if not isinstance(value,dict) or set(value)!=FIELDS or type(value.get('version')) is not int or value['version']!=1:_fail()
    if not isinstance(source,str) or re.fullmatch(r'[0-9a-f]{64}',source) is None or value['source_revision']!=source:_fail()
    start,end=value['issued_at'],value['expires_at']
    if any(type(v) not in {int,float} or not math.isfinite(v) for v in (start,end,now)):_fail()
    if not start<=now<end or not 0<end-start<=300:_fail()
    if not _ident(value['nonce']) or not _ident(value['conversation_id']):_fail()
    if value['prior_goal_id'] is not None and not _ident(value['prior_goal_id']):_fail()
    rows=value['messages']
    if not isinstance(rows,list) or len(rows)>MAX_MESSAGES:_fail()
    for row in rows:
        if (not isinstance(row,dict) or set(row)!={'role','text'} or row['role'] not in {'user','assistant'}
            or not isinstance(row['text'],str) or not row['text'].strip()):_fail()
    if len(_json(value))>MAX_BYTES-128:_fail()

def write_snapshot(root,snapshot,target_source,*,now=None):
    """Called by the trusted upgrade host AFTER independently settling all calls.

    Input is the host's authenticated GET /api/state snapshot. Only displayed
    role/text is extracted; extra GUI metadata is never part of model history.
    No truncation: an oversized history fails instead of silently losing text.
    """
    root,folder=_directory(root,True)
    if (not isinstance(snapshot,dict) or snapshot.get('active') is not False
        or not isinstance(snapshot.get('call'),dict) or snapshot['call'].get('held') is not False):_fail()
    messages=snapshot.get('messages')
    if not isinstance(messages,list):_fail()
    now=time.time() if now is None else now
    value=dict(version=1,source_revision=target_source,issued_at=now,expires_at=now+300,
               nonce=secrets.token_hex(16),conversation_id=snapshot.get('conversation_id'),
               prior_goal_id=snapshot.get('task_context',{}).get('goal_id'),
               messages=[dict(role=r.get('role'),text=r.get('text')) if isinstance(r,dict) else None for r in messages])
    _validate(value,target_source,now)
    encoded=_json(dict(value,mac=_mac(root,value)))
    if len(encoded)>MAX_BYTES:_fail()
    path=folder/(value['nonce']+'.json')
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
    with os.fdopen(fd,'wb') as handle:handle.write(encoded);handle.flush();os.fsync(handle.fileno())
    _syncdir(folder)
    return path

def restore_snapshot(root,path,console,target_source,*,now=None):
    """Import once, only into a new idle host after durable call recovery.

    The preserved marker is a consumption receipt, not a task authorization.
    This routine starts no model, microphone, MCP request or action.
    """
    root,folder=_directory(root)
    path=Path(path)
    if not path.is_absolute() or path.parent!=folder or path.is_symlink():_fail()
    if (console.calls.held or console.task is not None or console.mode!='standby'
        or console.shared.history or console.shared.goal_id is not None):_fail()
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
    with os.fdopen(fd,'rb') as handle:
        st=os.fstat(handle.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_size>MAX_BYTES:_fail()
        _private(st,0o600)
        current=path.lstat()
        if (current.st_dev,current.st_ino)!=(st.st_dev,st.st_ino):_fail()
        raw=handle.read(MAX_BYTES+1)
    if len(raw)>MAX_BYTES:_fail()
    value=json.loads(raw,object_pairs_hook=_pairs)
    if not isinstance(value,dict) or set(value)!=FIELDS|{'mac'}:_fail()
    mac=value.pop('mac')
    if not isinstance(mac,str) or not hmac.compare_digest(_mac(root,value),mac):_fail()
    _validate(value,target_source,time.time() if now is None else now)
    if path.name!=value['nonce']+'.json':_fail()
    # Exclusive durable marker prevents concurrent or later imports of this
    # signed snapshot. Leave both original and marker for host audit.
    marker=folder/(value['nonce']+'.consumed')
    fd=os.open(marker,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
    with os.fdopen(fd,'wb') as handle:
        handle.write(_json(dict(nonce=value['nonce'],source_revision=target_source,scope='conversation_text_only')))
        handle.flush();os.fsync(handle.fileno())
    _syncdir(folder)
    from onit_candidate.conversation import Context
    context=Context();context.conversation_id=value['conversation_id']
    context.history=[dict(type='user_input' if r['role']=='user' else 'model_output',
                          content=[dict(type='text',text=r['text'])]) for r in value['messages']]
    context.parent_goal_id=value['prior_goal_id']
    # New Context already has goal=None, idle, no clarification/last result,
    # and an empty retained-speech buffer. Do not re-create old task authority.
    console.shared=context;console.session_replies={}
    console.notice='Conversation text restored. Tap the microphone to continue.'
    return dict(conversation_id=context.conversation_id,messages=len(value['messages']),
                source_revision=target_source,prior_goal_id=value['prior_goal_id'],
                scope='conversation_text_only',voice_started=False)
