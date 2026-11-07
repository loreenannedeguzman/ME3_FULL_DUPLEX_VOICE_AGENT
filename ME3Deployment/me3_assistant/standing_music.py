"""Owner-authorized routine local media: independent monotonic accounting, no lifetime cap."""
import json,math,time
from pathlib import Path
from benchmark_m6.storage import atomic,read
from me3_assistant.guard import lock
from me3_assistant.library import plain_file

def authorized(grant):
    expected=['propose_music_control']
    if grant.get('routine_youtube_music') is True:expected.append('propose_youtube_control')
    if grant.get('routine_soundcloud_music') is True:expected.append('propose_stream_control')
    if grant.get('routine_whatsapp_messages') is True:expected.append('propose_whatsapp_message')
    if grant.get('routine_whatsapp_contact_messages') is True:expected.append('propose_contact_message')
    if grant.get('routine_whatsapp_window') is True:expected.append('propose_whatsapp_window')
    if grant.get('routine_whatsapp_calls') is True:expected.append('propose_whatsapp_call')
    if grant.get('routine_local_reminders') is True:expected.append('propose_reminder')
    if grant.get('routine_productivity') is True:expected.append('propose_productivity')
    if grant.get('routine_gmail_messages') is True:expected.append('propose_gmail_send')
    if grant.get('routine_gmail_reads') is True:expected.append('propose_gmail_read')
    if grant.get('routine_google_calendar') is True:expected.append('propose_google_calendar')
    return (grant.get('profile')=='standing' and grant.get('routine_local_music') is True
            and grant.get('allowed_actions')==expected and (grant.get('workspace_access') is False or (grant.get('workspace_access') is True and grant.get('workspace_scope')=='pi_artifacts_only')))

def validate(data):
    if (not isinstance(data,dict) or set(data)!={'version','music_control','attempts'} or data['version']!=1
        or type(data['music_control']) is not int or data['music_control']<0 or not isinstance(data['attempts'],list)
        or len(data['attempts'])>30 or any(type(x) not in {int,float} or not math.isfinite(x) or x<0 for x in data['attempts'])):raise ValueError('standing_music_counter_invalid')
    return data

def initialize(path):
    """Only at authorization activation. Never replaces an existing counter."""
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    if path.is_symlink() or path.exists() and (not plain_file(path.lstat()) or path.stat().st_size>16384):raise ValueError('standing_music_counter_untrusted')
    lockpath=path.with_suffix('.lock')
    if lockpath.is_symlink() or lockpath.exists() and not plain_file(lockpath.lstat()):raise ValueError('standing_music_counter_untrusted')
    with lock(path.with_suffix('.lock')):
        if path.exists():return validate(read(path))
        with path.open('x',encoding='utf-8') as f:json.dump(dict(version=1,music_control=0,attempts=[]),f)
        return validate(read(path))

class StandingMusicBudget:
    def __init__(self,base,grant,path,clock=time.time):
        if not authorized(grant):raise ValueError('standing_music_not_authorized')
        self.base=base;self.grant=grant;self.path=Path(path);self.clock=clock;self.observed=0
    def _state(self):
        self.base.permitted('asr') # Validate current authority and original ledgers, without spending.
        if self.path.is_symlink() or not self.path.exists() or not plain_file(self.path.lstat()) or self.path.stat().st_size>16384:raise ValueError('standing_music_counter_missing')
        data=validate(read(self.path))
        if data['music_control']<self.observed:raise ValueError('standing_music_counter_decreased')
        now=self.clock()
        if type(now) not in {int,float} or not math.isfinite(now) or now<0:raise ValueError('standing_music_clock_invalid')
        if any(t>now+1 for t in data['attempts']):raise ValueError('standing_music_clock_reversed')
        self.observed=data['music_control'];return data,[t for t in data['attempts'] if now-t<60]
    def _used(self,role='music_control'):
        if role!='music_control':return 0
        return self._state()[0]['music_control']
    def permitted(self,role):
        if role=='music_control':return len(self._state()[1])<30
        if role in {'reminder_control','commit_item'}:return False
        return self.base.permitted(role)
    def reserve(self,role):
        if role!='music_control':
            if role in {'reminder_control','commit_item'}:raise ValueError('standing_music_role_not_authorized')
            return self.base.reserve(role)
        lockpath=self.path.with_suffix('.lock')
        if lockpath.is_symlink() or lockpath.exists() and not plain_file(lockpath.lstat()):raise ValueError('standing_music_counter_untrusted')
        with lock(lockpath):
            data,attempts=self._state()
            if len(attempts)>=30:raise ValueError('standing_music_rate_limited')
            data['music_control']+=1;data['attempts']=attempts+[self.clock()];atomic(self.path,data);self.observed=data['music_control'];return self.observed

def compact_parameters(schema,branches):
    """Factor shared value constraints once; branches only restrict field presence."""
    import copy
    properties=copy.deepcopy(schema['properties'])
    for field,kind in [('track_id','string'),('track_ids','array'),('playlist_name','string')]:
        if field in properties:
            prop=properties[field];properties[field]=copy.deepcopy(next((x for x in prop.get('anyOf',[]) if x.get('type')==kind),prop))
    properties['volume']={'type':'integer','minimum':0,'maximum':100}
    def annotations(value):
        if isinstance(value,dict):return {k:annotations(v) for k,v in value.items() if k not in {'title','default'}}
        if isinstance(value,list):return [annotations(x) for x in value]
        return value
    choices=[]
    for branch in branches:
        choices.append({'properties':{k:(copy.deepcopy(v) if k=='command' else {}) for k,v in branch['properties'].items()},'required':branch['required'],'additionalProperties':False})
    return {'type':'object','properties':annotations(properties),'required':['command'],'additionalProperties':False,'anyOf':choices}
