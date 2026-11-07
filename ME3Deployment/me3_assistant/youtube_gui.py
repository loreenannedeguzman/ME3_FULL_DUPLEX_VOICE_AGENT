"""Attach official-player control to the existing loopback GUI security boundary."""
import json,os,re,secrets,uuid
from pathlib import Path
from me3_assistant.youtube import PlayerBridge,intent

def voice_active(console):return console.mode=='voice' and console.task is not None and not console.task.done()

async def bridge_command(console,data):
    action=data.get('action');b=console.youtube
    if action=='status':return b.public()
    owner=data.get('owner')
    if not isinstance(owner,str) or not re.fullmatch('[0-9a-f]{32}',owner):raise ValueError('invalid_youtube_owner')
    if action=='stop_owner':return await b.stop_owner(owner)
    if action=='inspect':
        if not b.public()['fresh']:raise ValueError('youtube_player_not_connected')
        return (await b.execute({'command':'inspect'},owner,uuid.uuid4().hex))['snapshot']
    if action!='execute' or not isinstance(data.get('clean'),dict) or not re.fullmatch('[0-9a-f]{32}',data.get('operation_id','')):raise ValueError('invalid_youtube_bridge_command')
    clean=data['clean'];normalized=intent(clean.get('command'),clean.get('video_id'),clean.get('volume'))
    if clean!=normalized:raise ValueError('invalid_youtube_bridge_intent')
    # Revalidate actual authority at each external effect; readback is read-only.
    grant,_=await __import__('asyncio').to_thread(console.gate,'text')
    if grant.get('routine_youtube_music') is not True:raise ValueError('youtube_not_authorized')
    return await b.execute(clean,owner,data['operation_id'])

def install_bridge(server,root):
    p=Path(root)/'.tmp/youtube-bridge.json';p.parent.mkdir(exist_ok=True)
    if p.is_symlink():raise ValueError('youtube_bridge_path_untrusted')
    server.youtube_token=secrets.token_hex(32)
    raw=json.dumps(dict(port=server.server_port,token=server.youtube_token,source_revision=server.console.source['revision']))+'\n'
    tmp=p.with_suffix('.new');fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w',encoding='utf-8',newline='\n') as f:f.write(raw)
    tmp.replace(p)
    return p
