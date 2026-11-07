"""GUI-owned call metadata sink, independent of the closed voice-session log.

Each append owns its descriptor only for that append. Voice cleanup cannot
close it. Arguments, tokens, proofs, recipients and conversation text are never
accepted as metadata. A logging failure retains the host privacy hold.
"""
import json,os,re,time
from pathlib import Path

FIELDS={'server','tool','generation','request_id','role','rpc_request_id',
        'discovered_tools','exposed_reads','pending','pending_count','count',
        'validation_reason','operation_id'}

class CallEvents:
    def __init__(self,root,gui_session,observer=None):
        self.root=Path(root).resolve()
        if not isinstance(gui_session,str) or not re.fullmatch('[0-9a-f]{32}',gui_session):raise ValueError('call_GUI_event_owner_required')
        self.gui_session=gui_session;self.observer=observer
        self.path=self.root/'evidence/assistant/call-control'/gui_session/'events.jsonl'
    def emit(self,event,**fields):
        if not isinstance(event,str) or not re.fullmatch('[a-z][a-z0-9_]{0,79}',event):raise ValueError('bounded_call_event_name_required')
        safe={}
        for k,v in fields.items():
            if k not in FIELDS:continue
            if isinstance(v,(bool,int)) or v is None:safe[k]=v
            elif isinstance(v,str) and len(v)<=160 and re.fullmatch('[a-zA-Z0-9_:. /-]*',v):safe[k]=v
        p=self.path;p.parent.mkdir(parents=True,exist_ok=True)
        if (not p.resolve().is_relative_to(self.root/'evidence/assistant/call-control')
            or any(q.is_symlink() for q in [p,*p.parents] if q!=self.root)
            or p.exists() and (not p.is_file() or p.stat().st_nlink!=1 or p.stat().st_size>2_000_000)):
            raise ValueError('private_GUI_call_event_log_required')
        body=(json.dumps(dict(event=event,wall_time=time.time(),monotonic_ns=time.monotonic_ns(),gui_session=self.gui_session,**safe),sort_keys=True,separators=(',',':'))+'\n').encode()
        fd=os.open(p,os.O_WRONLY|os.O_APPEND|os.O_CREAT|getattr(os,'O_NOFOLLOW',0),0o600)
        try:
            if os.write(fd,body)!=len(body):raise ValueError('incomplete_call_event_append')
            os.fsync(fd)
        finally:os.close(fd)
        if self.observer:self.observer(event,**safe)
