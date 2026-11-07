"""The existing confirmed/verified action owner controls YouTube effects."""
import re
from me3_assistant.youtube import digest,intent

class YouTubeControl:
    budget_role='music_control'
    def __init__(self,registry,spec):
        self.registry=registry;self.spec=spec;self.server=spec['server']
        names={'prepare_player_control','commit_player_control','read_player_operation'}
        if not all((self.server,n) in registry.schemas for n in names) or names&set(registry.sessions[self.server][1]['read_tools']):raise ValueError('youtube_lifecycle_missing_or_public')
    def declaration(self):
        branches=[]
        for cmd,fields in [('play',['video']),('pause',[]),('resume',[]),('stop',[]),('volume',['volume'])]:
            allowed={'command',*fields}|({'volume'} if cmd=='play' else set());props={'command':{'type':'string','enum':[cmd]}}
            if 'video' in allowed:props['video']={'type':'string','minLength':1,'maxLength':500}
            if 'volume' in allowed:props['volume']={'type':'integer','minimum':0,'maximum':100}
            branches.append({'type':'object','properties':props,'required':['command',*fields],'additionalProperties':False})
        parameters={'type':'object','properties':{'command':{'type':'string','enum':['play','pause','resume','stop','volume']},'video':{'type':'string','minLength':1,'maxLength':500},'volume':{'type':'integer','minimum':0,'maximum':100}},'required':['command'],'additionalProperties':False,'anyOf':[{'properties':{k:(v if k=='command' else {}) for k,v in b['properties'].items()},'required':b['required'],'additionalProperties':False} for b in branches]}
        return {'name':self.spec['name'],'description':'Visible YouTube: play real link/ID; pause/resume/stop; volume0–100. Text mode. Controller authorizes/verifies.','parameters':parameters}
    async def prepare(self,args,g,operation_id):
        if getattr(self.registry,'session_grant',{}).get('routine_youtube_music') is not True:raise ValueError('youtube_not_authorized')
        clean=intent(args['command'],args.get('video'),args.get('volume'));payload={**args,'operation_id':operation_id}
        data=await self.registry.rpc(self.server,'prepare_player_control',g,payload)
        if data.get('operation_id')!=operation_id or data.get('expected_digest')!=digest(clean) or any(data.get(k)!=v for k,v in clean.items()) or not re.fullmatch('[0-9a-f]{32}',data.get('token','')):raise ValueError('youtube_proposal_mismatch')
        return data,digest(clean),'YouTube '+clean['command']+((' '+clean['video_id']) if 'video_id' in clean else '')
    async def execute(self,p,g):return await self.registry.rpc(self.server,'commit_player_control',g,{'operation_id':p['operation_id'],'token':p['proposal']['token'],'confirmation':'confirm'})
    async def verify(self,p,g):
        data=await self.registry.rpc(self.server,'read_player_operation',g,{'operation_id':p['operation_id']});row=data.get('row');s=data.get('snapshot',{})
        if not row or row.get('state')!='COMPLETED':return False
        if data.get('verification_source')!='fresh_youtube_player_API_readback' or row.get('operation_id')!=p['operation_id'] or row.get('expected_digest')!=p['expected_digest'] or digest(row['intent'])!=p['expected_digest'] or not s.get('fresh') or s.get('sequence',-1)<=row.get('sequence',0):raise ValueError('youtube_verification_mismatch')
        c=row['intent'];expected={'play':{'playing'},'pause':{'paused','ended'},'resume':{'playing'},'stop':{'stopped','unstarted','cued','paused','ended'},'volume':{'playing','paused','cued','ended'}}[c['command']]
        return not s.get('error') and s.get('state') in expected and (c['command']=='stop' or s.get('visible')) and ('video_id' not in c or c['video_id']==s.get('video_id')) and ('volume' not in c or c['volume']==s.get('volume'))
    def success_message(self,p):
        c=p['proposal'];return f"YouTube player volume is {c['volume']} percent; player state checked." if c['command']=='volume' else {'play':'YouTube playback started; player state checked.','pause':'YouTube is paused; player state checked.','resume':'YouTube playback resumed; player state checked.','stop':'YouTube is stopped; player state checked.'}[c['command']]
