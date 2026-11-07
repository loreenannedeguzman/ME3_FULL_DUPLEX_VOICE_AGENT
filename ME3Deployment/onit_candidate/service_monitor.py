"""Explicit, bounded GUI-only service metric watches; no planner or startup job."""
import asyncio,base64,copy,hashlib,json,math,os,re,stat,time,uuid
from pathlib import Path

NAME='manage_service_monitor'
UNITS={'count':('count',1),'bytes':('bytes',1),'seconds':('seconds',1),'milliseconds':('seconds',.001),'minutes':('seconds',60)}
COMMANDS=('baseline','compare','configure','restart','status','stop')

def need(ok,reason):
    if not ok:raise ValueError(reason)

def declaration():
    result=dict(name=NAME,description='Read one owned service /metrics JSON metric, save a genuine baseline, or compare a fresh reading in compatible units. Endpoint: {"metrics":{"name":{"value":finite_number,"unit":"count|bytes|seconds|milliseconds|minutes","observed_at":UTC_unix_seconds}}}; observations older than10s or over2s future are unavailable. Only configure on a current user request: baseline ID, condition, cadence5..60s and lifetime10..60s. GUI orange notices only, no speech/messages/accounts. Host reviews configuration and requires separate confirmation. At most12 samples and one active watch; first unavailable stops the watch, no retry/automatic restart. status/stop need returned run ID; restart requires fresh confirmation and a new run. Stopping a watch never stops its backend.',parameters=dict(type='object',properties={
        'command':dict(type='string',enum=list(COMMANDS)),
        'service_id':dict(type='string',pattern='^[0-9a-f]{32}$'),
        'workspace_sha256':dict(type='string',pattern='^[0-9a-f]{64}$'),
        'metric':dict(type='string',pattern='^[a-z][a-z0-9_]{0,47}$'),
        'unit':dict(type='string',enum=list(UNITS)),
        'baseline_id':dict(type='string',pattern='^[0-9a-f]{32}$'),
        'run_id':dict(type='string',pattern='^[0-9a-f]{32}$'),
        'operator':dict(type='string',enum=['increase_at_least','decrease_at_least']),
        'threshold':dict(type='number',minimum=0),
        'cadence_seconds':dict(type='integer',minimum=5,maximum=60),
        'lifetime_seconds':dict(type='integer',minimum=10,maximum=60),
        'destination':dict(type='string',const='gui_notice')},required=['command'],additionalProperties=False))
    # The model-visible shape is the exact contract dispatch enforces.
    fields=dict(baseline=('service_id','workspace_sha256','metric','unit'),
                compare=('baseline_id',),
                configure=('baseline_id','operator','threshold','cadence_seconds','lifetime_seconds','destination'),
                restart=('run_id',),status=('run_id',),stop=('run_id',))
    schema=result['parameters'];properties=schema['properties']
    schema['oneOf']=[dict(properties={**{n:properties[n] for n in names},
                                    'command':dict(type='string',const=command)},
                         required=['command',*names],additionalProperties=False)
                     for command,names in fields.items()]
    return result

def finite(value):return type(value) in (int,float) and math.isfinite(value) and abs(value)<=1e15
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()

class ServiceMonitor:
    def __init__(self,context,*,clock=time.monotonic,wall=time.time,sleep=asyncio.sleep):
        self.context=context;self.clock=clock;self.wall=wall;self.sleep=sleep
        self.pending=None;self.task=None;self.active=None;self.root=None;self.factory=None
        self.source=None;self.lock=asyncio.Lock();self.sample_lock=asyncio.Lock();self.notice=None;self.baselines={};self.runs={};self.workers=set()
    async def io(self,function,*args):
        # A canceled owner cannot abandon an in-flight private write or source guard.
        task=asyncio.create_task(asyncio.to_thread(function,*args));self.workers.add(task)
        try:
            try:return await asyncio.shield(task)
            except asyncio.CancelledError:
                while not task.done():
                    try:await asyncio.shield(task)
                    except asyncio.CancelledError:pass
                task.result()
                raise
        finally:self.workers.discard(task)
    def bind(self,root,factory):
        root=Path(root).absolute()
        need(self.root in (None,root),'monitor_root_changed')
        self.root=root;self.factory=factory
    def identity(self):return self.context.conversation_id,self.context.revision
    def directory(self):
        need(self.root is not None,'monitor_not_bound');path=self.root/'data/service-monitors'
        need(not any(p.is_symlink() for p in [path,*path.parents]),'monitor_directory_link')
        need(path.parent.is_dir(),'monitor_parent_missing')
        path.mkdir(mode=0o700,exist_ok=True)
        info=path.stat();need(stat.S_ISDIR(info.st_mode),'monitor_directory_required')
        if os.name=='posix':need(info.st_uid==os.getuid() and not info.st_mode&0o077,'monitor_private_directory_required')
        return path
    def save(self,kind,key,value):
        need(kind in {'baseline','run'} and re.fullmatch('[0-9a-f]{32}',key),'monitor_record_identity')
        path=self.directory()/f'{kind}-{key}.json';raw=canonical(value)
        need(len(raw)<=32768,'monitor_record_bound')
        if path.exists():
            info=path.lstat();need(stat.S_ISREG(info.st_mode) and info.st_nlink==1 and not path.is_symlink(),'monitor_plain_record_required')
        else:need(len(list(path.parent.iterdir()))<64,'monitor_history_limit')
        temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
        fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
        try:
            with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
            os.replace(temp,path)
        finally:
            if temp.exists():temp.unlink()
    def loaded(self,kind,key):
        need(isinstance(key,str) and re.fullmatch('[0-9a-f]{32}',key),'monitor_record_id_required')
        path=self.directory()/f'{kind}-{key}.json';info=path.lstat()
        need(stat.S_ISREG(info.st_mode) and info.st_nlink==1 and not path.is_symlink() and info.st_size<=32768,'monitor_plain_record_required')
        if os.name=='posix':need(info.st_uid==os.getuid() and not info.st_mode&0o077,'monitor_private_record_required')
        value=json.loads(path.read_bytes());need(value.get(kind+'_id')==key and value.get('conversation_id')==self.context.conversation_id,'monitor_record_owner_mismatch')
        return value
    def binding(self,args):
        need(set(args)=={'service_id','workspace_sha256','metric','unit'},'monitor_exact_metric_fields')
        need(isinstance(args['service_id'],str) and re.fullmatch('[0-9a-f]{32}',args['service_id']) and isinstance(args['workspace_sha256'],str) and re.fullmatch('[0-9a-f]{64}',args['workspace_sha256']),'monitor_exact_service_required')
        need(isinstance(args['metric'],str) and re.fullmatch('[a-z][a-z0-9_]{0,47}',args['metric']) and args['unit'] in UNITS,'monitor_metric_unit_required')
        return dict(args)
    async def sample(self,binding,expected=None):
        async with self.sample_lock:
            return await self._sample(binding,expected)
    async def _sample(self,binding,expected=None):
        stamp=dict(requested_at=self.wall(),monotonic=self.clock())
        try:
            runtime,source=await self.io(self.factory)
            if expected:need(source==expected['source_revision'],'monitor_source_changed')
            row=await asyncio.wait_for(runtime.view(binding['service_id'],binding['workspace_sha256'],method='GET',path='/metrics'),4)
            need(row.get('ok') is True and row.get('status')=='view' and row.get('outcome_verified') is True and row.get('owned_namespace_closed') is False and row.get('namespace_local_only') is True and row.get('host_listening_port') is False,'monitor_live_proof_required')
            need(row.get('service_id')==binding['service_id'] and row.get('workspace_manifest_sha256')==binding['workspace_sha256'] and row.get('request_path')=='/metrics' and row.get('method')=='GET','monitor_service_binding_changed')
            need(row.get('http_status')==200 and row.get('content_type')=='application/json','monitor_JSON_metrics_required')
            raw=base64.b64decode(row['body_base64'],validate=True)
            need(len(raw)<=65536 and len(raw)==row['body_bytes'] and hashlib.sha256(raw).hexdigest()==row['body_sha256'],'monitor_body_proof_required')
            birth=dict(pid_namespace_inode=row.get('pid_namespace_inode'),init_birth=row.get('init_birth'),environment_id=row.get('environment_id'))
            need(type(birth['pid_namespace_inode']) is int and isinstance(birth['init_birth'],dict),'monitor_birth_required')
            if expected:need(birth==expected['birth'],'monitor_service_birth_changed')
            data=json.loads(raw);metric=data.get('metrics',{}).get(binding['metric'])
            need(isinstance(metric,dict) and set(metric)=={'value','unit','observed_at'} and finite(metric['value']) and metric['unit'] in UNITS and finite(metric['observed_at']),'monitor_finite_declared_metric_required')
            received=self.wall()
            need(received-metric['observed_at']<=10,'monitor_stale_observation')
            need(metric['observed_at']-received<=2,'monitor_future_timestamp')
            family,scale=UNITS[metric['unit']];wanted,divisor=UNITS[binding['unit']]
            need(family==wanted,'monitor_incompatible_units')
            value=metric['value']*scale/divisor;need(finite(value),'monitor_converted_value_bound')
            return dict(**stamp,observed_at=metric['observed_at'],received_at=received,state='available',value=value,unit=binding['unit'],reported_unit=metric['unit'],source_revision=source,birth=birth,body_sha256=row['body_sha256'])
        except asyncio.CancelledError:raise
        except (ValueError,KeyError,TypeError,OSError,TimeoutError) as exc:
            reason=str(exc) if type(exc) is ValueError and re.fullmatch('monitor_[a-z_]+',str(exc)) else type(exc).__name__
            return dict(**stamp,observed_at=None,received_at=self.wall(),state='unavailable',reason=reason,comparison=None,automatic_retry=False)
    @staticmethod
    def comparison(baseline,sample):
        if sample['state']!='available':return dict(state='unavailable',delta=None)
        need(sample['source_revision']==baseline['observation']['source_revision'] and sample['birth']==baseline['observation']['birth'] and sample['unit']==baseline['binding']['unit'],'monitor_comparison_identity_changed')
        return dict(state='compared',baseline_value=baseline['observation']['value'],current_value=sample['value'],delta=sample['value']-baseline['observation']['value'],unit=sample['unit'],baseline_observed_at=baseline['observation']['observed_at'],current_observed_at=sample['observed_at'])
    async def dispatch(self,args,current):
        async with self.lock:
            need(current(),'monitor_current_turn_required');command=args.get('command');need(command in COMMANDS,'monitor_command_required')
            values={k:v for k,v in args.items() if k!='command'}
            if command=='baseline':
                need(not self.pending and (self.task is None or self.task.done()),'monitor_one_active_or_pending')
                binding=self.binding(values);sample=await self.sample(binding);need(current(),'monitor_current_turn_required')
                if sample['state']!='available':return dict(ok=False,status='unavailable',observation=sample,baseline_saved=False)
                key=uuid.uuid4().hex;row=dict(baseline_id=key,conversation_id=self.context.conversation_id,binding=binding,observation=sample)
                await self.io(self.save,'baseline',key,row);self.baselines[key]=row
                return dict(ok=True,status='baseline_saved',**copy.deepcopy(row))
            if command=='compare':
                need(set(values)=={'baseline_id'},'monitor_exact_baseline_required');base=await self.io(self.loaded,'baseline',values['baseline_id'])
                sample=await self.sample(base['binding'],base['observation']);need(current(),'monitor_current_turn_required')
                return dict(ok=sample['state']=='available',status='comparison',baseline_id=base['baseline_id'],observation=sample,comparison=self.comparison(base,sample))
            if command in {'status','stop'}:
                need(set(values)=={'run_id'},'monitor_exact_run_required')
                row=self.runs.get(values['run_id']) or await self.io(self.loaded,'run',values['run_id'])
                need(row['conversation_id']==self.context.conversation_id,'monitor_record_owner_mismatch')
                if row['run_id']!=self.active and row['state']=='running':
                    row.update(state='interrupted',settled=True,automatic_restart=False)
                    await self.io(self.save,'run',row['run_id'],copy.deepcopy(row))
                self.runs[row['run_id']]=row
                if command=='stop':
                    await self.stop(values['run_id']);row=self.runs.get(values['run_id']) or row
                return dict(ok=True,status='monitor_'+row['state'],run=copy.deepcopy(row),backend_stopped=False,heard=False)
            need(not self.pending and (self.task is None or self.task.done()),'monitor_one_active_or_pending')
            if command=='restart':
                need(set(values)=={'run_id'},'monitor_exact_run_required');old=self.runs.get(values['run_id']) or await self.io(self.loaded,'run',values['run_id'])
                need(old['state'] in {'stopped','expired','failed','interrupted'},'monitor_stop_before_restart');config=copy.deepcopy(old['config']);previous=old['run_id']
            else:config=values;previous=None
            fields={'baseline_id','operator','threshold','cadence_seconds','lifetime_seconds','destination'}
            need(set(config)==fields,'monitor_exact_configuration_required')
            need(config['operator'] in {'increase_at_least','decrease_at_least'} and finite(config['threshold']) and config['threshold']>=0,'monitor_condition_required')
            c,l=config['cadence_seconds'],config['lifetime_seconds']
            need(type(c) is int and 5<=c<=60 and type(l) is int and 10<=l<=60 and c<=l and math.ceil(l/c)<=12 and config['destination']=='gui_notice','monitor_bounded_GUI_configuration_required')
            base=await self.io(self.loaded,'baseline',config['baseline_id']);sample=await self.sample(base['binding'],base['observation']);need(current(),'monitor_current_turn_required')
            need(sample['state']=='available','monitor_fresh_configuration_read_required')
            label=base['binding'];prompt=f"Watch {label['metric']} for service {label['service_id']} every {c} seconds for {l} seconds, compared with its saved baseline; notify once in the GUI if it {config['operator'].replace('_',' ')} {config['threshold']} {label['unit']}. No speech or messages. Please confirm or cancel."
            self.pending=dict(operation_id=uuid.uuid4().hex,expected_digest=hashlib.sha256(canonical(config)).hexdigest(),prompt=prompt,expires=self.clock()+120,context=self.identity(),config=copy.deepcopy(config),previous_run_id=previous)
            return dict(status='needs_confirmation',reply=prompt,execution_started=False,GUI_only=True)
    async def confirm(self,current):
        async with self.lock:
            p=self.pending;need(p and p['context']==self.identity() and self.clock()<p['expires'] and current(),'monitor_current_review_required')
            self.pending=None;base=await self.io(self.loaded,'baseline',p['config']['baseline_id'])
            sample=await self.sample(base['binding'],base['observation']);need(current(),'monitor_current_turn_required')
            need(sample['state']=='available','monitor_fresh_start_read_required')
            need(self.task is None or self.task.done(),'monitor_one_active_or_pending')
            key=uuid.uuid4().hex;row=dict(run_id=key,conversation_id=self.context.conversation_id,source_revision=sample['source_revision'],config=p['config'],previous_run_id=p['previous_run_id'],state='running',started_at=self.wall(),samples=[],notification=None,automatic_restart=False,backend_stopped=False)
            try:
                await self.io(self.save,'run',key,copy.deepcopy(row));need(current(),'monitor_current_turn_required')
            except BaseException:
                row.update(state='interrupted',settled=True,ended_at=self.wall())
                await self.io(self.save,'run',key,copy.deepcopy(row));raise
            self.runs[key]=row;self.active=key;self.notice=None
            self.task=asyncio.create_task(self.run(row,base));return dict(ok=True,status='monitor_started',run_id=key,GUI_only=True,heard=False,backend_stopped=False)
    async def run(self,row,base):
        started=self.clock();config=row['config'];maximum=math.ceil(config['lifetime_seconds']/config['cadence_seconds'])
        try:
            for index in range(maximum):
                if self.clock()>=started+config['lifetime_seconds']:break
                sample=await self.sample(base['binding'],base['observation'])
                if self.clock()>=started+config['lifetime_seconds']:break
                result=self.comparison(base,sample);row['samples'].append(dict(index=index,observation=sample,comparison=result))
                if sample['state']!='available':
                    row['state']='failed';row['failure_reason']='observation_unavailable'
                    notice=dict(notification_id=uuid.uuid4().hex,run_id=row['run_id'],conversation_id=row['conversation_id'],context_revision=self.context.revision,destination='gui_notice',kind='unavailable',text='The service watch stopped because its next observation was unavailable. Nothing was retried; the backend was left unchanged.',heard=False)
                    row['failure_notice']=notice;await self.io(self.save,'run',row['run_id'],copy.deepcopy(row));self.notice=notice;return
                if result['state']=='compared' and row['notification'] is None:
                    delta=result['delta'];matched=delta>=config['threshold'] if config['operator']=='increase_at_least' else -delta>=config['threshold']
                    if matched:
                        notice=dict(notification_id=uuid.uuid4().hex,run_id=row['run_id'],conversation_id=row['conversation_id'],context_revision=self.context.revision,destination='gui_notice',text=f"Monitor: {base['binding']['metric']} changed by {delta:g} {base['binding']['unit']} from the saved baseline.",observed_at=sample['observed_at'],heard=False)
                        row['notification']=notice;await self.io(self.save,'run',row['run_id'],copy.deepcopy(row));self.notice=notice
                await self.io(self.save,'run',row['run_id'],copy.deepcopy(row))
                target=min(started+(index+1)*config['cadence_seconds'],started+config['lifetime_seconds']);await self.sleep(max(0,target-self.clock()))
            row['state']='expired'
        except asyncio.CancelledError:row['state']='stopped';raise
        except Exception as exc:row['state']='failed';row['failure_type']=type(exc).__name__
        finally:
            row['ended_at']=self.wall();row['settled']=True;await self.io(self.save,'run',row['run_id'],copy.deepcopy(row))
            if self.active==row['run_id']:self.active=None
    async def stop(self,key=None):
        self.pending=None
        if key is not None:need(key in self.runs or key==self.active,'monitor_current_run_required')
        if self.task is not None and not self.task.done() and (key is None or key==self.active):
            self.task.cancel();settled=asyncio.gather(self.task,return_exceptions=True);cancelled=False
            while not settled.done():
                try:await asyncio.shield(settled)
                except asyncio.CancelledError:cancelled=True
            outcomes=settled.result()
            need(all(not isinstance(v,BaseException) or isinstance(v,asyncio.CancelledError) for v in outcomes),'monitor_cleanup_unverified')
            if cancelled:raise asyncio.CancelledError()
        if key in self.runs and self.runs[key]['state']=='running':
            self.runs[key].update(state='stopped',settled=True,ended_at=self.wall());await self.io(self.save,'run',key,copy.deepcopy(self.runs[key]))
    def notification(self):
        return copy.deepcopy(self.notice) if self.notice and self.notice['conversation_id']==self.context.conversation_id and self.notice['context_revision']==self.context.revision else None
    def metadata(self):
        rows=[r for r in self.runs.values() if r['conversation_id']==self.context.conversation_id]
        current=self.runs.get(self.active) if self.active else rows[-1] if rows else None
        return dict(baselines=[dict(baseline_id=b['baseline_id'],**b['binding'],observed_at=b['observation']['observed_at']) for b in list(self.baselines.values())[-8:] if b['conversation_id']==self.context.conversation_id],
                    latest_run=dict(run_id=current['run_id'],state=current['state'],baseline_id=current['config']['baseline_id'],destination='gui_notice',cached=True) if current else None,
                    policy='Cached monitor bookkeeping identifies the configured watch only. Use status for a current settled observation; no cached backend liveness, speech, account action or restart authority.')
