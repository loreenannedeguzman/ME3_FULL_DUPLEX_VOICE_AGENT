"""Confirmation and independent read-back adapter for reversible music controls."""
import copy,re
from me3_assistant.music import intent,fingerprint

class MusicControl:
    budget_role='music_control'
    def __init__(self,registry,spec):
        self.registry=registry;self.spec=spec;self.server=spec['server']
        names={'prepare_music_control','commit_music_control','read_music_operation'}
        if any((self.server,n) not in registry.schemas for n in names):raise ValueError('music_lifecycle_missing')
        if names&set(registry.sessions[self.server][1]['read_tools']):raise ValueError('music_lifecycle_exposed')
    def declaration(self):
        schema=self.registry.exposed_schema(self.server,'prepare_music_control');props=schema['properties'];branches=[]
        required={'play':['track_id'],'pause':[],'resume':[],'stop':[],'volume':['volume'],'enqueue':['track_ids'],'replace_queue':['track_ids'],'clear_queue':[],'skip':[],'save_playlist':['track_ids','playlist_name'],'play_playlist':['playlist_name']}
        for command,fields in required.items():
            branch=copy.deepcopy(schema);permitted={'command',*fields}|({'volume'} if command in {'play','play_playlist'} else set())
            branch['properties']={k:v for k,v in branch['properties'].items() if k in permitted};branch['properties']['command']={'type':'string','enum':[command]};branch['required']=['command',*fields]
            for field,kind in [('track_id','string'),('track_ids','array'),('playlist_name','string')]:
                if field in branch['properties']:branch['properties'][field]=copy.deepcopy(next((x for x in props[field].get('anyOf',[]) if x.get('type')==kind),props[field]))
            if command in {'play','volume','play_playlist'}:branch['properties']['volume']={'type':'integer','minimum':0,'maximum':100}
            branches.append(branch)
        from me3_assistant.standing_music import compact_parameters
        return {'name':self.spec['name'],'description':'Local library controls by exact IDs/names. Queues continue after current playback; stopped enqueue waits. Saved playlists persist. Controller authorizes/verifies.','parameters':compact_parameters(schema,branches)}
    async def prepare(self,args,g,operation_id):
        try:clean=intent(args['command'],args.get('track_id'),args.get('volume'),args.get('track_ids'),args.get('playlist_name'))
        except ValueError as exc:
            reason=str(exc)
            if reason not in {'invalid_track_id','unsupported_music_control','unexpected_music_track','music_volume_limit','unexpected_music_volume','invalid_playlist_name','invalid_queue_tracks','unexpected_queue_tracks','unexpected_playlist_name','unexpected_queue_arguments'}:reason='invalid_music_arguments'
            self.registry.events.emit('assistant_music_arguments_rejected',generation=g,validation_reason=reason,argument_values_retained=False,execution_started=False)
            raise
        data=await self.registry.rpc(self.server,'prepare_music_control',g,{**clean,'operation_id':operation_id})
        if data.get('operation_id')!=operation_id or data.get('expected_digest')!=fingerprint(clean) or any(data.get(k)!=v for k,v in clean.items()) or not re.fullmatch('[0-9a-f]{32}',data.get('token','')):raise ValueError('music_proposal_mismatch')
        descriptions={'play':lambda:f"Play {clean['track_id']} at player volume {clean['volume']} percent",'pause':lambda:'Pause this session’s music','resume':lambda:'Resume this session’s music','stop':lambda:'Stop this session’s music','volume':lambda:f"Set this session’s music volume to {clean['volume']} percent",'enqueue':lambda:f"Add these ordered tracks to the queue: {', '.join(clean['track_ids'])}; continue after current playback",'replace_queue':lambda:f"Replace upcoming music with: {', '.join(clean['track_ids'])}; continue after current playback",'clear_queue':lambda:'Clear upcoming tracks while leaving the current track unchanged','skip':lambda:'Skip to the next queued track','save_playlist':lambda:f"Save playlist {clean['playlist_name']} with these ordered tracks: {', '.join(clean['track_ids'])}",'play_playlist':lambda:f"Play saved playlist {clean['playlist_name']} in order at player volume {clean['volume']} percent"}
        text=descriptions[clean['command']]()
        return data,fingerprint(clean),text
    async def execute(self,p,g):return await self.registry.rpc(self.server,'commit_music_control',g,{'operation_id':p['operation_id'],'token':p['proposal']['token'],'confirmation':'confirm'})
    async def verify(self,p,g):
        data=await self.registry.rpc(self.server,'read_music_operation',g,{'operation_id':p['operation_id']});row=data.get('row');state=data.get('snapshot',{})
        if not row:return False
        if data.get('verification_source')!='fresh_music_bus_state' or row.get('operation_id')!=p['operation_id'] or row.get('expected_digest')!=p['expected_digest'] or fingerprint(row['intent'])!=p['expected_digest'] or row.get('state')!='COMPLETED':raise ValueError('music_verification_mismatch')
        command=row['intent']['command']
        if command in {'enqueue','replace_queue','clear_queue','skip','save_playlist','play_playlist'}:
            result=row.get('result',{})
            if command=='save_playlist':
                actual=data.get('playlist',{})
                valid=(actual.get('name')==row['intent']['playlist_name'] and actual.get('track_ids')==row['intent']['track_ids'] and type(actual.get('revision')) is int and actual['revision']>=result.get('playlist_revision',2**63))
                if valid:p['verified_state']='playlist_saved'
                return valid
            queue=data.get('queue',{});revision=queue.get('revision')
            if type(revision) is not int or revision<result.get('queue_revision',2**63):return False
            if command=='clear_queue':return queue.get('track_ids')==[] and queue.get('autoplay') is False
            if queue.get('program_id')!=result.get('program_id') or queue.get('error'):return False
            if revision==result['queue_revision'] and fingerprint(queue.get('track_ids'))!=result['upcoming_digest']:return False
            if command in {'skip','play_playlist'} and (state.get('state') not in {'playing','ended'} or state.get('consumed_frames',0)<1):return False
            if command=='play_playlist' and state.get('volume_percent')!=row['intent']['volume']:return False
            return True
        if row.get('epoch')!=state.get('epoch'):raise ValueError('music_verification_mismatch')
        command=row['intent']['command'];expected={'play':{'playing','ended'},'pause':{'paused'},'resume':{'playing','ended'},'stop':{'stopped'},'volume':{'playing','paused'}}[command]
        if state.get('state') not in expected or command=='volume' and state.get('volume_percent')!=row['intent']['volume']:return False
        if command=='play' and (state.get('track_id')!=row['intent']['track_id'] or state.get('consumed_frames',0)<1):return False
        p['verified_state']=state['state'];return True
    def success_message(self,p):
        cmd=p['proposal']['command']
        if cmd=='save_playlist':return f"Saved playlist {p['proposal']['playlist_name']}; I checked its ordered tracks."
        if cmd=='play_playlist':return f"Started playlist {p['proposal']['playlist_name']}; player and queue checked."
        if cmd in {'enqueue','replace_queue'}:return 'The upcoming music queue is updated; I checked its state.'
        if cmd=='clear_queue':return 'Upcoming tracks are cleared; the current track was left unchanged.'
        if cmd=='skip':return 'Skipped to the next queued track; player state checked.'
        if p.get('verified_state')=='ended':return 'The track finished. I checked the player state.'
        return {'play':f"Playback started for {p['proposal']['track_id']}. I checked the player state.",'pause':'Music is paused; player state checked.','resume':'Music resumed; player state checked.','stop':'Music is stopped; player state checked.','volume':f"Music player volume is {p['proposal']['volume']} percent; state checked."}[cmd]
