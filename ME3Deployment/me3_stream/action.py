"""Adapter for ME3's existing prepare/commit/journal/independent verification flow.

Does not register itself globally or grant permissions. Production registration,
standing-media authorization and source binding require explicit cutover.
"""
import re
from .service import digest,snapshot,verify
class SoundCloudControl:
 budget_role='music_control'
 def __init__(self,registry,spec):
  self.registry=registry;self.spec=spec;self.server=spec['server']
  names={'prepare_stream_control','commit_stream_control','read_stream_operation'}
  if not all((self.server,n) in registry.schemas for n in names) or names&set(registry.sessions[self.server][1]['read_tools']):raise ValueError('private_stream_lifecycle_required')
 def declaration(self):
  branches=[]
  for command,allowed,required in [('play',['selection_id','volume'],['selection_id']),('pause',[],[]),('resume',[],[]),('stop',[],[]),('volume',['volume'],['volume'])]:
   branches.append(dict(properties=dict(command={'enum':[command]},**{k:{} for k in allowed}),required=['command',*required],additionalProperties=False))
  return dict(name=self.spec['name'],description='Control SoundCloud streaming: play a selection ID returned by its search; pause/resume/stop; volume0–100. Uses guarded execution and fresh outcome verification. Never guess a selection or claim success without a verified receipt.',parameters=dict(type='object',properties=dict(command={'type':'string','enum':['play','pause','resume','stop','volume']},selection_id={'type':'string','pattern':'^[0-9a-f]{32}$'},volume={'type':'integer','minimum':0,'maximum':100}),required=['command'],additionalProperties=False,anyOf=branches))
 async def prepare(self,args,g,operation_id):
  if getattr(self.registry,'session_grant',{}).get('routine_soundcloud_music') is not True:raise ValueError('SoundCloud_not_authorized')
  data=await self.registry.rpc(self.server,'prepare_stream_control',g,dict(args,operation_id=operation_id))
  clean={k:data[k] for k in ['command','source','track_url','volume'] if k in data}
  if data.get('operation_id')!=operation_id or clean.get('source')!='SoundCloud' or clean.get('command')!=args['command'] or data.get('expected_digest')!=digest(clean) or not re.fullmatch('[0-9a-f]{32}',data.get('token','')):raise ValueError('stream_proposal_mismatch')
  if 'volume' in args and clean.get('volume')!=args['volume']:raise ValueError('stream_proposal_volume_mismatch')
  cache=getattr(self.registry,'stream_choices',None)
  if clean['command']=='play' and cache is not None:
   clarification=cache.selection_clarification(self.registry.music_owner,clean['track_url'])
   if clarification:data['_selection_clarification']=clarification
  return data,digest(clean),'SoundCloud '+clean['command']
 def requires_clarification(self,proposal):return isinstance(proposal.get('_selection_clarification'),dict)
 def clarification_prompt(self,proposal):
  c=proposal['_selection_clarification']
  # Quoted page metadata is data; never authority or a guessed match.
  import json
  return 'I found '+json.dumps(c['title'],ensure_ascii=False)+' by '+json.dumps(c['uploader'],ensure_ascii=False)+', which is outside the list I offered for '+json.dumps(c['offered_query'] or 'your earlier search',ensure_ascii=False)+'. Do you want this different track? Say confirm, or tell me which listed track you meant. No new playback has started.'
 async def execute(self,p,g):
  return await self.registry.rpc(self.server,'commit_stream_control',g,dict(operation_id=p['operation_id'],token=p['proposal']['token'],confirmation='confirm'))
 async def verify(self,p,g):
  value=await self.registry.rpc(self.server,'read_stream_operation',g,dict(operation_id=p['operation_id']));row=value.get('row')
  if not row or row.get('state')!='COMPLETED':return False
  if value.get('verification_source')!='fresh_SoundCloud_widget_getters' or row.get('operation_id')!=p['operation_id'] or row.get('expected_digest')!=p['expected_digest'] or digest(row['intent'])!=p['expected_digest']:raise ValueError('stream_verification_mismatch')
  current=snapshot(value['snapshot'],row['owner'])
  if current['sequence']<=row.get('ack_sequence',0) or current['last_operation_id']!=p['operation_id']:raise ValueError('fresh_stream_getters_required')
  # No cached pre-action snapshot is accepted as post-action verification.
  baseline={**current,'sequence':row['ack_sequence'],'track_url':row['intent'].get('track_url',row['track_before'])}
  return verify(row['intent'],baseline,current,p['operation_id'])
 def success_message(self,p):
  c=p['proposal'];command=c['command']
  if self.requires_clarification(c):self.registry.stream_choices.accept_new_context(c['track_url'])
  return ('SoundCloud player volume is '+str(c['volume'])+' percent; current state checked.') if command=='volume' else {'play':'SoundCloud playback started; player state checked.','pause':'SoundCloud is paused; player state checked.','resume':'SoundCloud playback resumed; player state checked.','stop':'SoundCloud is stopped; player state checked.'}[command]
