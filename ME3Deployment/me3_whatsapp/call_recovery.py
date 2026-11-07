"""No-device startup audit; adopt a persisted hold without dialing/restarting."""
import os
from pathlib import Path
from .native_call_end import processes

async def audit(root,record,new_gui_session):
    root=Path(root).resolve();proof=record.get('proof',{})
    # A replacement GUI may hold an old call, but cannot assume that the old
    # capture/ASR/controller processes disappeared merely because HTTP stopped.
    rows=processes();native=set()
    for p in rows:
        args=p.get('args',[])
        if any(a.startswith('--user-data-dir=') and a.endswith('/.local/share/me3-whatsapp-profile') for a in args):native.add(p['pid'])
        if p['pid']==os.getpid():continue
        try:cwd=(Path('/proc')/str(p['pid'])/'cwd').resolve()
        except OSError:raise ValueError('call_recovery_process_identity_unknown')
        if cwd!=root:continue
        if any(a in {'onit_candidate.gui','onit_candidate.voice','voice_agent','speech_mcp.piper_worker','onit_candidate.asr_server','onit_candidate.child','me3_agent.voice','me3_assistant.voice'} for a in args):
            raise ValueError('previous_ME3_voice_process_still_present')
    # Descendants of the exact private native browser may own call streams.
    # They stay held; this audit does not claim those streams/call ended.
    changed=True
    while changed:
        before=set(native)
        native.update(p['pid'] for p in rows if p.get('parent') in native)
        changed=native!=before
    from me3_stream.audio import graph
    for n in await graph():
        if n.get('type')!='PipeWire:Interface:Node':continue
        p=n.get('info',{}).get('props',{})
        if not str(p.get('media.class','')).startswith('Stream/'):continue
        pid=str(p.get('application.process.id',''))
        if not pid.isdigit() or int(pid) not in native:
            raise ValueError('previous_ME3_audio_closure_not_independently_established')
    return dict(operation_id=proof.get('operation_id'),new_gui_session=new_gui_session,
                old_ME3_processes_closed=True,old_ME3_audio_closed=True,
                authenticated_GUI_adoption=True,dialed=False,automatic_restart=False)
