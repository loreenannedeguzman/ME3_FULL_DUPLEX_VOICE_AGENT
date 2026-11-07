"""Benchmark-only packet/sample observations, with no conversation authority.

The two monotonic domains are retained, never subtracted. A host event is
bounded on the Rig sample clock by actual two-way causal packet anchors.
"""
import asyncio,hashlib,json,threading,time
from collections import deque
SAMPLE_NS=62500
EARLY_SAMPLES=512
LATE_SAMPLES=128000
ALIGNMENT_CEILING_NS=32000000
def need(v,c):
 if not v:raise ValueError(c)
def digest(raw):return hashlib.sha256(raw).hexdigest()
class Trace:
 def __init__(self,clock_domain,*,clock=time.monotonic_ns,maximum_rows=50000):
  need(type(clock_domain) is str and bool(clock_domain),'explicit_clock_domain_required')
  self.clock_domain=clock_domain;self.clock=clock;self.rows=[];self.maximum_rows=maximum_rows;self.failed=None;self.lock=threading.RLock();self.session_id=None
  self.pending_packet=None;self.pending_control=None;self.input_samples=0;self.input_ordinal=0;self.output_ordinal=0;self.control_ordinal=0;self.trial_id=None;self.connection_id=None;self.connections={}
 def add(self,kind,**value):
  with self.lock:
   if len(self.rows)>=self.maximum_rows:self.failed='observation_capacity_exhausted';return
   self.rows.append(dict(kind=kind,sequence=len(self.rows),connection_id=self.connection_id,**value))
 def snapshot(self):
  with self.lock:return json.loads(json.dumps(dict(clock_domain=self.clock_domain,session_id=self.session_id,connections=self.connections,failed=self.failed,rows=self.rows)))

def attach_core(core,trace,*,session_id):
 """Attach before startup. Delegate every byte/control exactly once, unchanged."""
 need(type(session_id) is str and bool(session_id),'exact_bridge_session_required')
 need(not getattr(core,'_measurement_attached',False),'one_core_observer_required');core._measurement_attached=True;trace.session_id=session_id;trace.connection_id=session_id;trace.connections[session_id]=session_id
 feed,pcm,control=core.feed_input,core.send_pcm,core.send_control
 def observed_feed(data):
  before=trace.clock();start=core.input_samples+len(core.pending)//2+core.dropped_before_ready_bytes//2
  first=core.input_samples;dropped=core.dropped_before_ready_bytes
  result=feed(data)
  trace.add('host_input',before_ns=before,after_ns=trace.clock(),wire_start=start,wire_samples=len(data)//2,sha256=digest(data),admitted_start=first,admitted_end=core.input_samples,dropped_before=dropped,dropped_after=core.dropped_before_ready_bytes)
  return result
 async def observed_pcm(data):
  ordinal=trace.output_ordinal;trace.output_ordinal+=1;before=trace.clock();epoch=core.state.buffer.epoch
  generation=core.describe_epoch(epoch).get('generation');state='uncertain'
  try:result=await pcm(data);state='completed';return result
  finally:trace.add('host_output',ordinal=ordinal,before_ns=before,after_ns=trace.clock(),wire_samples=len(data)//2,sha256=digest(data),epoch=epoch,generation=generation,state=state)
 async def observed_control(value):
  ordinal=trace.control_ordinal if value.get('event')=='clear' else None
  if ordinal is not None:trace.control_ordinal+=1
  before=trace.clock();state='uncertain'
  try:result=await control(value);state='completed';return result
  finally:trace.add('host_control',ordinal=ordinal,event=value.get('event'),before_ns=before,after_ns=trace.clock(),state=state)
 core.feed_input=observed_feed;core.send_pcm=observed_pcm;core.send_control=observed_control
 return trace

class TaggedPlayout:
 """Metadata sidecar for the ORIGINAL course playout, including trim/clear/pad."""
 def __init__(self,delegate,trace,position):self.delegate=delegate;self.trace=trace;self.position=position;self.tags=deque();self.lock=threading.RLock();self.size=0
 def __getattr__(self,name):return getattr(self.delegate,name)
 @property
 def clears(self):return self.delegate.clears
 @clears.setter
 def clears(self,value):self.delegate.clears=value
 def _take(self,n):
  result=[]
  while n and self.tags:
   row=self.tags[0];count=min(n,row['count']);result.append(dict(ordinal=row['ordinal'],offset=row['offset'],count=count));row['offset']+=count;row['count']-=count;self.size-=count;n-=count
   if not row['count']:self.tags.popleft()
  return result,n
 def push(self,audio):
  with self.lock:
   self.delegate.push(audio);packet=self.trace.pending_packet
   if packet is None:self.trace.failed='unbound_playout_push'
   self.tags.append(dict(ordinal=packet['ordinal'] if packet else None,offset=0,count=len(audio)));self.size+=len(audio)
   if self.size>self.delegate.max:
    dropped,_=self._take(self.size-self.delegate.max);self.trace.add('rig_overflow',dropped=dropped)
   self.trace.add('rig_push',ordinal=packet['ordinal'] if packet else None,rendered_samples=len(audio))
 def pop(self,n):
  with self.lock:
   before=self.trace.clock();audio=self.delegate.pop(n);spans,padded=self._take(n);trial,pos=self.position()
   self.trace.add('rig_pop',trial_id=trial,sample_start=pos,sample_end=pos+n if pos is not None else None,spans=spans,padded_samples=padded,before_ns=before,after_ns=self.trace.clock())
   return audio
 def clear(self):
  with self.lock:
   self.delegate.clear();removed,_=self._take(self.size);self.trace.add('rig_clear',control_ordinal=self.trace.pending_control,discarded=removed)

def observed_rig(base,trace):
 """Compose outside the existing guarded_rig. The original course is unedited."""
 class Rig(base):
  def __init__(self,*args,**kwargs):
   super().__init__(*args,**kwargs);self.playout=TaggedPlayout(self.playout,trace,self._measurement_position)
  def _measurement_position(self):
   with self._sess_lock:
    session=self._sess
    return (getattr(session,'measurement_trial_id',None),session.pos) if session is not None else (None,None)
  def _measurement_receive_bound(self):
   with self._sess_lock:session=self._sess
   if session is None:return None,None,None
   with session.measurement_tick_lock:
    return session.measurement_trial_id,session.measurement_recorded_position+(self.chunk if session.measurement_tick_active else 0),session.measurement_tick_active
  def bind_trial(self,trial_id):
   need(type(trial_id) is str and bool(trial_id),'explicit_trial_id_required');trace.trial_id=trial_id
  class _Session(base._Session):
   def __init__(self,rig,script):
    super().__init__(rig,script);self.measurement_trial_id=trace.trial_id;need(self.measurement_trial_id is not None,'bind_trial_before_run')
    self.measurement_tick_lock=threading.RLock();self.measurement_recorded_position=self.pos;self.measurement_tick_active=False
   def next_out(self,n):
    with self.measurement_tick_lock:self.measurement_tick_active=True
    before=trace.clock();audio=super().next_out(n);trace.add('rig_tick_start',trial_id=self.measurement_trial_id,sample_start=self.pos,count=n,before_ns=before,after_ns=trace.clock());return audio
   def feed_in(self,audio):
    before=trace.clock();start=self.pos;super().feed_in(audio)
    with self.measurement_tick_lock:self.measurement_recorded_position=self.pos;self.measurement_tick_active=False
    trace.add('rig_tick_end',trial_id=self.measurement_trial_id,sample_start=start,sample_end=self.pos,before_ns=before,after_ns=trace.clock())
  def _handler(self,connection):
   if self.connected.is_set():return super()._handler(connection)
   trace.connection_id='connection-'+str(len(trace.connections));trace.connections[trace.connection_id]=None
   trace.input_samples=trace.input_ordinal=trace.output_ordinal=trace.control_ordinal=0
   rig=self
   class Proxy:
    def __getattr__(self,name):return getattr(connection,name)
    def send(self,message,*args,**kwargs):
     if not isinstance(message,(bytes,bytearray)):return connection.send(message,*args,**kwargs)
     ordinal=trace.input_ordinal;trace.input_ordinal+=1;start=trace.input_samples;trace.input_samples+=len(message)//2;trial,pos=rig._measurement_position();before=trace.clock()
     result=connection.send(message,*args,**kwargs)
     trace.add('rig_input',ordinal=ordinal,wire_start=start,wire_samples=len(message)//2,sha256=digest(message),trial_id=trial,sample_start=pos,before_ns=before,after_ns=trace.clock());return result
    def __iter__(self):
     for message in connection:
      before=trace.clock();trial,pos=rig._measurement_position()
      if isinstance(message,(bytes,bytearray)):
       upper_trial,upper,active=rig._measurement_receive_bound()
       ordinal=trace.output_ordinal;trace.output_ordinal+=1;packet=dict(ordinal=ordinal,wire_samples=len(message)//2,sha256=digest(message),trial_id=trial,sample_position=pos,sample_upper_bound=upper if upper_trial==trial else None,tick_active=active,before_ns=before,after_ns=trace.clock());trace.add('rig_output',**packet);trace.pending_packet=packet
       try:yield message
       finally:trace.pending_packet=None
      else:
       try:value=json.loads(message)
       except (ValueError,TypeError):value={}
       if type(value) is dict and value.get('event')=='me3_ready':trace.session_id=value.get('session');trace.connections[trace.connection_id]=trace.session_id
       if type(value) is dict and value.get('event')=='clear':trace.pending_control=trace.control_ordinal;trace.control_ordinal+=1
       try:yield message
       finally:trace.pending_control=None
   return super()._handler(Proxy())
 return Rig

def join(host,rig):
 """Exact directional wire joins; no timestamp subtraction between machines."""
 errors=[];session=host.session_id
 hrows=[r for r in host.rows if host.connections.get(r['connection_id'])==session]
 rrows=[r for r in rig.rows if rig.connections.get(r['connection_id'])==session]
 if not session or session not in rig.connections.values():errors.append('unbound_session_identity')
 def unique(rows,key):
  result={}
  for row in rows:
   k=key(row)
   if k in result:errors.append('duplicate_packet_identity')
   result[k]=row
  return result
 hinput=unique([r for r in hrows if r['kind']=='host_input'],lambda r:r['wire_start'])
 rinput=unique([r for r in rrows if r['kind']=='rig_input'],lambda r:r['wire_start'])
 hout=unique([r for r in hrows if r['kind']=='host_output'],lambda r:r['ordinal'])
 rout=unique([r for r in rrows if r['kind']=='rig_output'],lambda r:r['ordinal'])
 inputs=[];outputs=[]
 for source,destination,target,direction in ((rinput,hinput,inputs,'input'),(hout,rout,outputs,'output')):
  for key,d in destination.items():
   s=source.get(key)
   if s is None or (s['wire_samples'],s['sha256'])!=(d['wire_samples'],d['sha256']) or s.get('state','completed')!='completed':errors.append(direction+'_packet_unmatched');continue
   target.append(dict(host=d if direction=='input' else s,rig=s if direction=='input' else d))
 if set(hout)!=set(rout):errors.append('output_packet_count_disagreement')
 if any(k not in hinput and r['trial_id'] is not None for k,r in rinput.items()):errors.append('trial_input_without_admission_trace')
 hcontrol=unique([r for r in hrows if r['kind']=='host_control' and r['event']=='clear'],lambda r:r['ordinal'])
 rcontrol=unique([r for r in rrows if r['kind']=='rig_clear' and r['control_ordinal'] is not None],lambda r:r['control_ordinal'])
 if set(hcontrol)!=set(rcontrol) or any(r['state']!='completed' for r in hcontrol.values()):errors.append('clear_lineage_unverified')
 if host.failed or rig.failed:errors.append(host.failed or rig.failed)
 return dict(status='PACKET_LINEAGE_JOINED' if not errors else 'INVALID_LINEAGE',session_id=session,errors=errors,inputs=inputs,outputs=outputs,host_clock_domain=host.clock_domain,rig_clock_domain=rig.clock_domain)

def event_interval(joined,host_ns,trial_id,*,rig_chunk=512,ceiling_ns=ALIGNMENT_CEILING_NS):
 """Causal interval on the recording grid. Never midpoint/offset substitution."""
 result=dict(trial_id=trial_id,host_monotonic_ns=host_ns,host_clock_domain=joined['host_clock_domain'],rig_clock_domain=joined['rig_clock_domain'],alignment_ceiling_ns=ceiling_ns)
 if joined['status']!='PACKET_LINEAGE_JOINED':return dict(**result,status='INVALID_ALIGNMENT',reason='MISSING_TRACE')
 lower=[p for p in joined['inputs'] if p['rig']['trial_id']==trial_id and p['rig']['sample_start'] is not None and p['host']['after_ns']<=host_ns]
 upper=[p for p in joined['outputs'] if p['rig']['trial_id']==trial_id and p['rig'].get('sample_upper_bound') is not None and p['host']['before_ns']>=host_ns]
 if not lower or not upper:return dict(**result,status='INVALID_ALIGNMENT',reason='missing_causal_anchor')
 left=max(lower,key=lambda p:p['rig']['sample_start']);right=min(upper,key=lambda p:p['rig']['sample_upper_bound'])
 lo=left['rig']['sample_start'];hi=right['rig']['sample_upper_bound']
 width=(hi-lo)*SAMPLE_NS
 return dict(**result,status='ALIGNED_INTERVAL' if 0<=width<=ceiling_ns else 'INVALID_ALIGNMENT',reason=None if 0<=width<=ceiling_ns else 'observed_interval_exceeds_ceiling',sample_lo=lo,sample_hi=hi,interval_width_ns=width,lower_packet=left['rig']['ordinal'],upper_packet=right['rig']['ordinal'],cross_host_monotonic_subtracted=False)

def epoch_samples(joined,rig):
 """Packet-origin ranges on actual Rig pop, not speech or acoustic labels."""
 need(joined['status']=='PACKET_LINEAGE_JOINED','valid_lineage_required');packets={p['rig']['ordinal']:p['host'] for p in joined['outputs']};rows=[]
 for pop in (r for r in rig.rows if r['kind']=='rig_pop' and r['trial_id'] is not None and rig.connections.get(r['connection_id'])==joined['session_id']):
  at=pop['sample_start']
  for span in pop['spans']:
   packet=packets.get(span['ordinal']);need(packet is not None,'popped_packet_without_host_origin')
   rows.append(dict(trial_id=pop['trial_id'],sample_start=at,sample_end=at+span['count'],packet_ordinal=span['ordinal'],packet_render_offset=span['offset'],epoch=packet['epoch'],generation=packet['generation']));at+=span['count']
 return rows

def match_opportunities(opportunities,predictions):
 """Bounded one-to-one matching; ambiguous interval assignments stay unscored.

 Timings never establish directedness, successful continuation or authority.
 Caller supplies independently validated labels and capture/generation lineage.
 """
 need(len(opportunities)<=8 and len(predictions)<=16,'bounded_matching_set_required')
 need(all(o.get('label')=='directed_completed_request' for o in opportunities),'independent_directed_request_labels_required')
 if any(p.get('disposition') not in ('takeover','adopt','ignore','ack','hold') for p in predictions):return dict(status='NOT_MEASURED',reason='INVALID_DISPOSITION')
 ignored=sum(p.get('disposition') not in ('takeover','adopt') for p in predictions)
 predictions=[p for p in predictions if p.get('disposition') in ('takeover','adopt')]
 if any(p['interval']['status']!='ALIGNED_INTERVAL' or p.get('lineage_verified') is not True or type(p['interval'].get('sample_lo')) is not int or type(p['interval'].get('sample_hi')) is not int or p['interval']['sample_lo']>p['interval']['sample_hi'] for p in predictions):return dict(status='NOT_MEASURED',reason='INVALID_ALIGNMENT_OR_LINEAGE')
 edges={};premature=[]
 for j,p in enumerate(predictions):
  edges[j]=[];lo=p['interval']['sample_lo'];hi=p['interval']['sample_hi']
  for i,o in enumerate(opportunities):
   if p['trial_id']!=o['trial_id'] or p['capture_id']!=o['capture_id'] or p['generation']!=o['generation'] or p['disposition'] not in ('takeover','adopt'):continue
   start,end=o['start_sample'],o['end_sample'];anchor=start
   if lo<start:premature.append(dict(prediction=p['id'],opportunity=o['id'],definite=hi<start))
   if lo>=start-EARLY_SAMPLES and hi<=end+LATE_SAMPLES:
    low=0 if lo<=anchor<=hi else min(abs(lo-anchor),abs(hi-anchor));high=max(abs(lo-anchor),abs(hi-anchor))
    edges[j].append((i,low,high))
 candidates=[]
 def walk(j,used,pairs,low,high):
  need(len(candidates)<20000,'matching_assignment_capacity')
  if j==len(predictions):candidates.append((pairs,low,high));return
  walk(j+1,used,pairs,low,high)
  for i,a,b in edges[j]:
   if i not in used:walk(j+1,used|{i},pairs+[(j,i)],low+a,high+b)
 walk(0,set(),[],0,0);maximum=max(len(c[0]) for c in candidates);best=[c for c in candidates if len(c[0])==maximum]
 best.sort(key=lambda c:(c[1],[(opportunities[i]['id'],predictions[j]['id']) for j,i in c[0]]));selected=best[0]
 if any(selected[2]>=other[1] and not (selected[1]==selected[2]==other[1]==other[2]) for other in best[1:]):return dict(status='NOT_MEASURED',reason='AMBIGUOUS_INTERVAL_ASSIGNMENT',premature=premature)
 pairs=[dict(prediction=predictions[j]['id'],opportunity=opportunities[i]['id']) for j,i in selected[0]]
 return dict(status='MATCHED',TP=len(pairs),FP=len(predictions)-len(pairs),FN=len(opportunities)-len(pairs),pairs=pairs,premature=premature,ignored_noncommand_dispositions=ignored,early_tolerance_ns=EARLY_SAMPLES*SAMPLE_NS,late_matching_window_ns=LATE_SAMPLES*SAMPLE_NS)
