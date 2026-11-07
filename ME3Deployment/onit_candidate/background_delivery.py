"""Inactive host glue with explicit corrected actual424 core adapter; no runtime pass.

The strict reviewed adapter provides enqueue(record), claim(binding),
settle(claim,outcome), discard(task_id), close(). Host proof remains authority;
queue claims are bookkeeping only. No fallback/core simulation is selected.
"""
import asyncio,copy,hashlib,inspect,json,re,time,uuid
from pipecat.frames.frames import InterruptionFrame
from pathlib import Path
from onit_candidate.protocol import CandidateError

READY_PROMPT='Your app is running and ready to view in Artifacts. Would you like me to open it?'

def need(v,c):
 if not v:raise CandidateError(c)

class BackgroundDelivery:
 def __init__(self,processor,conversation,source_revision,stop_requested,*,core,core_sha256,call_held):
  from onit_candidate.background_core_adapter import BackgroundCoreAdapter
  need(type(core) is BackgroundCoreAdapter,'exact_reviewed_background_core_adapter_required')
  from me3_assistant.guard import verify_sources
  path=Path(inspect.getfile(type(core))).resolve();root=Path(conversation.root).resolve()
  need(path.is_relative_to(root/'onit_candidate') and not path.is_symlink() and hashlib.sha256(path.read_bytes()).hexdigest()==core_sha256,'exact_actual_core_file_required')
  source=verify_sources(root);need(source['revision']==source_revision and source['source_hashes'].get(path.relative_to(root).as_posix())==core_sha256,'approved_core_current_source_binding_required')
  need(conversation.voice and conversation.controller is processor.controller and callable(call_held),'background_normal_voice_owner_required')
  self.p=processor;self.c=conversation;self.source=source_revision;self.stop=stop_requested;self.call_held=call_held;self.core=core
  self.cid=conversation.context.conversation_id;self.records={};self.watch=None;self.closed=False;self.idle_stamp=None;self.settled=set();self.notice_task=None;self.notice_binding=None;self.onset_task=None;self.reset_token=object()
  self.source_file=root/'evidence/assistant/candidate-source.json';self.source_stamp=self.stamp()
  self.registry=conversation.engine.registry;need(getattr(self.registry,'background_runtime_observer',None) is None,'background_observer_already_bound')
  self.registry.background_runtime_observer=self;self.p.background_delivery=self;self.c.background_delivery=self
  self.core.bind_host(current=self.live,idle=self.idle,binding=self.binding)
  self.output=self.p.output_transport;self.output_original=self.output.process_frame
  async def observed(frame,direction):return await self.observe_output(frame,direction)
  self.output_observed=observed;self.output.process_frame=observed
  self.worker=asyncio.create_task(self.drain())
 def live(self):
  return not self.closed and not self.stop.is_set() and not self.p.closing and not self.p.controller.closed and not self.p.pipeline_failed.is_set() and not self.c.failed.is_set() and self.c.context.conversation_id==self.cid and self.stamp()==self.source_stamp
 def stamp(self):
  try:
   value=self.source_file.stat();return value.st_dev,value.st_ino,value.st_size,value.st_mtime_ns
  except OSError:return None
 def event(self,name,**fields):
  self.c.events.emit(name,conversation_id=self.cid,source_revision=self.source,**fields)
 def prepare(self,server,tool,g,args):
  request=args['request'];command=request['command']
  if command=='stop':return dict(command='stop',service_id=request['service_id'])
  if command!='start' or request.get('background_ready') is not True:return None
  need(self.live() and not self.call_held() and not self.c.pending and self.c.engine.current(g),'background_current_start_owner_required')
  if self.watch is not None and self.watch.task is not None and self.watch.task.done() and not self.records:self.watch=None
  need(self.watch is None and not self.records,'one_pending_background_service_required')
  ctx=self.c.context;need(ctx.goal_id is not None,'background_actual_task_owner_required')
  return dict(command='start',conversation_id=self.cid,source_revision=self.source,goal_id=ctx.goal_id,task_revision=ctx.revision,origin_generation=self.c.active_generation,native_generation=g,request=copy.deepcopy(request))
 def uncertain(self,origin,data=None,error_type=None):
  key=data.get('service_id') if type(data) is dict else None
  self.event('background_start_adoption_unverified',service_id=key if isinstance(key,str) and re.fullmatch('[0-9a-f]{32}',key) else None,origin_generation=origin.get('origin_generation'),error_type=error_type,readiness=False,action_retried=False,backend_stopped=False)
 async def complete(self,origin,data):
  if origin is None:return
  if origin['command']=='stop':
   key=origin['service_id']
   if data.get('service_id')==key and data.get('outcome_verified') is True and data.get('owned_namespace_closed') is True:
    self.core.discard(key)
    if self.watch and self.watch.binding and self.watch.binding['owner']['service_id']==key:await self.watch.close();self.watch=None
    self.records={n:r for n,r in self.records.items() if r['service_id']!=key}
   return
  if not self.live() or not self.c.current(origin['origin_generation']) or self.c.context.goal_id!=origin['goal_id'] or self.c.context.revision!=origin['task_revision']:
   self.uncertain(origin,data,'owner_changed_after_committed_start');return
  from me3_assistant.workspace_runtime import OwnedReadinessWatch
  try:
   runtime,source=await asyncio.to_thread(self.c.runtime_delivery);need(source==self.source and self.live(),'background_source_changed')
   watch=OwnedReadinessWatch(runtime,current=self.live,publish=self.publish,before_cleanup=self.before_cleanup)
   await watch.begin(data,**{k:origin[k] for k in ('conversation_id','source_revision','goal_id','origin_generation','task_revision')});self.watch=watch
   self.event('background_owned_starting_adopted',service_id=data['service_id'],origin_generation=origin['origin_generation'],ready=False,backend_start_transferred=True)
  except asyncio.CancelledError:raise
  except Exception as exc:self.uncertain(origin,data,type(exc).__name__)
 async def before_cleanup(self,owner):
  need(self.live(),'background_cleanup_session_changed')
  runtime,source=await asyncio.to_thread(self.c.runtime_delivery)
  proof=await asyncio.to_thread(runtime.readiness_identity,owner['service_id'],owner['workspace_manifest_sha256'])
  need(source==self.source and proof==owner and self.live(),'background_cleanup_fresh_source_or_owner_changed')
 async def publish(self,receipt):
  if not self.live():return
  if receipt.get('kind')!='service_ready' or receipt.get('verified') is not True or receipt.get('ready_offer_allowed') is not True:
   self.event('background_readiness_notice',service_id=receipt.get('service_id'),kind=receipt.get('kind'),verified=receipt.get('verified') is True,error_type=receipt.get('error_type'),backend_unchanged=receipt.get('backend_unchanged'),success_speech=False)
   return
  runtime,source=await asyncio.to_thread(self.c.runtime_delivery)
  need(source==self.source and receipt['conversation_id']==self.cid and receipt['source_revision']==self.source,'background_ready_source_conversation_changed')
  proof=await asyncio.to_thread(runtime.readiness_identity,receipt['service_id'],receipt['workspace_manifest_sha256'])
  need(proof['file_sha256']==receipt['file_sha256'] and hashlib.sha256(json.dumps(proof,sort_keys=True,separators=(',',':')).encode()).hexdigest()==receipt['owner_sha256'],'background_ready_version_birth_changed')
  if not self.live():return
  record={**copy.deepcopy(receipt),'notice_id':uuid.uuid4().hex,'task_id':receipt['service_id'],'text':READY_PROMPT,'expires_monotonic':time.monotonic()+60,'action_authority':False}
  need(len(self.records)<2,'background_notice_capacity')
  if self.core.enqueue(record) is True:self.records[record['notice_id']]=record;self.event('background_ready_enqueued',service_id=record['service_id'],notice_id=record['notice_id'],origin_generation=record['origin_generation'])
 def retire_expired(self):
  expired=self.core.retire_expired(now=time.monotonic())
  for key in expired:self.records.pop(key,None)
  return expired
 def buffer_state(self):
  buffer=self.p.state.buffer;delegate=buffer.delegate
  with buffer.lock:
   with delegate._lock:return delegate.epoch,delegate._size,bool(buffer.paused)
 def quiet(self):
  p=self.p
  return (not p.speaking and p.starting is None and not p.pcm and not p.overflow and not p.probing
   and getattr(p.last_vad,'name',None)=='QUIET' and all(v is None or v.done() for v in (p.decision,p.probe,p.cue)))
 def idle(self):
  p=self.p;c=p.controller;epoch,size,paused=self.buffer_state();task=c.task
  return (self.live() and not self.call_held() and not self.c.pending and self.quiet() and getattr(p.ownership,'value',None)=='listening'
   and (task is None or task.done()) and self.c.engine.task is None and p.output_task is None and p.output_generation is None
   and p.floor_response is None and not p.floor_retiring and (p.floor_retirement is None or p.floor_retirement.done())
   and not p.output_lock.locked() and p.gate.ready.is_set() and not paused and size==0)
 def binding(self):return dict(conversation_id=self.cid,source_revision=self.source,owner_generation=self.p.controller.clock.current,processor_token=self.p.token,output_epoch=self.p.state.buffer.epoch,context_revision=self.c.context.revision,engine_generation=self.c.engine.generation)
 def owned(self,b):
  return (self.live() and not self.call_held() and self.binding()==b and self.c.current(b['owner_generation']) and self.quiet()
   and self.p.gate.ready.is_set() and not self.p.state.buffer.paused and not self.c.pending and asyncio.current_task() is self.p.controller.task)
 def reset_owned(self,b,task):
  return bool(self.live() and self.binding()==b and task in (self.notice_task,self.onset_task) and task is self.p.controller.task and self.c.current(b['owner_generation']))
 async def observe_output(self,frame,direction):
  request=getattr(frame,'m3_background_reset',None)
  if request is None:return await self.output_original(frame,direction)
  try:
   if request.get('token') is not self.reset_token or not self.reset_owned(request['binding'],request['task']):return
   before=self.p.state.buffer.epoch
   await self.output_original(frame,direction)
   request['accepted']=self.p.state.buffer.epoch>before and self.p.controller.clock.current==request['binding']['owner_generation']
  finally:request['done'].set()
 async def user_onset(self):
  # Called inline at actual provisional VAD STARTING/SPEAKING. It never
  # advances a generation, calls provider.interrupt, or touches another turn.
  task=self.notice_task;b=self.notice_binding
  if task is None or task.done() or task is self.onset_task:return
  self.onset_task=task
  current=b is not None and self.reset_owned(b,task)
  if current:self.p.gate.suspend()
  task.cancel()
  if not current:return
  try:
   async with asyncio.timeout(3):await asyncio.gather(task,return_exceptions=True)
   if not self.reset_owned(b,task):return
   request=dict(token=self.reset_token,binding=dict(b),task=task,done=asyncio.Event(),accepted=False)
   frame=InterruptionFrame();frame.m3_generation=b['owner_generation'];frame.m3_background_reset=request
   async with asyncio.timeout(2):
    await self.p.push_frame(frame);await request['done'].wait()
   if not request['accepted']:
    if self.reset_owned(b,task):raise CandidateError('background_onset_retirement_unverified')
    return
   if self.live() and self.p.controller.task is task and self.p.controller.clock.current==b['owner_generation']:self.p.gate.resume()
   self.event('background_notice_yielded_at_VAD_onset',generation=b['owner_generation'],replacement_generation_created=False,output_retirement_verified=True,acoustic_cessation_measured=False,automatic_retry=False)
  except asyncio.CancelledError:raise
  except Exception as exc:
   # A newer turn may have legitimately filtered/rejected the old reset.
   # Never turn that old request's timeout into a failure of the new owner.
   if self.live() and self.p.controller.task is task and self.p.controller.clock.current==b['owner_generation']:
    self.p.pipeline_failed.set();self.event('background_notice_lane_failed',error_type=type(exc).__name__,success_speech=False,automatic_retry=False)
 def settle(self,claim,outcome):
  key=claim['notice_id']
  if key in self.settled:return
  self.settled.add(key);self.core.settle(claim,outcome);self.records.pop(key,None)
 async def drain(self):
  try:
   while self.live():
    self.retire_expired()
    if not self.idle():self.idle_stamp=None;await asyncio.sleep(.05);continue
    b=self.binding();stamp=(b,self.p.state.callback_count)
    if self.idle_stamp is None or self.idle_stamp[0]!=b:self.idle_stamp=stamp;await asyncio.sleep(.05);continue
    if self.p.state.callback_count<self.idle_stamp[1]+2:await asyncio.sleep(.05);continue
    # Two real empty-queue callback boundaries, not a physical silence claim.
    claim=self.core.claim(b)
    if claim is None:await asyncio.sleep(.05);continue
    need(type(claim) is dict and claim.get('notice_id') in self.records,'approved_core_claim_does_not_identify_owned_receipt')
    self.notice_binding=dict(b);self.p.controller.spawn(self.consume(claim,b));self.notice_task=self.p.controller.task
    self.notice_task.add_done_callback(lambda task,claimed=claim:self.settle(claimed,'canceled_before_or_during_notice') if task.cancelled() else None)
    await asyncio.gather(self.notice_task,return_exceptions=True);self.notice_task=None;self.idle_stamp=None;self.notice_binding=None
  except asyncio.CancelledError:raise
  except Exception as exc:self.event('background_notice_lane_failed',error_type=type(exc).__name__,success_speech=False,automatic_retry=False)
 async def consume(self,claim,b):
  record=self.records[claim['notice_id']];outcome='unverified';pending=None
  try:
   need(self.owned(b) and time.monotonic()<record['expires_monotonic'],'background_claim_stale')
   runtime,source=await asyncio.to_thread(self.c.runtime_delivery);need(source==self.source and self.owned(b),'background_delivery_source_changed')
   preview=self.c.context.runtime_preview;args=dict(service_id=record['service_id'],workspace_sha256=record['workspace_manifest_sha256'])
   await preview.live(runtime,args);row,_=await preview.fetch(runtime,args)
   need(row['content_type'].split(';')[0]=='text/html' and row['http_status']==200 and self.owned(b),'background_ready_view_unavailable')
   proof=await asyncio.to_thread(runtime.readiness_identity,record['service_id'],record['workspace_manifest_sha256'])
   need(hashlib.sha256(json.dumps(proof,sort_keys=True,separators=(',',':')).encode()).hexdigest()==record['owner_sha256'] and self.owned(b),'background_notice_owner_changed')
   played=await self.p.controller.background_notice_speech(record['text'],b['owner_generation'],lambda:self.owned(b))
   need(played and self.owned(b),'background_notice_not_completed')
   deadline=asyncio.get_running_loop().time()+3
   while self.buffer_state()[1]:
    need(self.owned(b) and asyncio.get_running_loop().time()<deadline,'background_notice_drain_unverified');await asyncio.sleep(.02)
   need(self.owned(b),'background_notice_owner_changed_after_drain')
   # This is a separate actual spoken row, not B's projected/retained reply.
   self.c.context.history=self.c.context.history+[dict(type='model_output',background_notice=True,notice_id=record['notice_id'],service_id=record['service_id'],content=[dict(type='text',text=record['text'])])]
   offer=await preview.offer(args,runtime,self.source,_host_current=lambda:self.owned(b) and time.monotonic()<record['expires_monotonic']);pending=preview.pending
   need(self.live() and self.binding()==b and self.c.current(b['owner_generation']) and preview.pending is pending and offer['reply']==record['text'],'background_offer_after_delivery_changed')
   outcome='software_completed';self.event('background_notice_complete',notice_id=record['notice_id'],service_id=record['service_id'],generation=b['owner_generation'],origin_generation=record['origin_generation'],preview_opened=False,acoustic_completion_measured=False)
  except asyncio.CancelledError:outcome='interrupted';raise
  except Exception as exc:self.event('background_notice_unverified',notice_id=record['notice_id'],service_id=record['service_id'],error_type=type(exc).__name__,success_speech=False,automatic_retry=False)
  finally:
   if outcome!='software_completed' and pending is not None and self.c.context.runtime_preview.pending is pending:self.c.context.runtime_preview.pending=None
   self.settle(claim,outcome)
 async def close(self):
  if self.closed:return
  self.closed=True
  if self.registry.background_runtime_observer is self:self.registry.background_runtime_observer=None
  if self.notice_task and not self.notice_task.done():self.notice_task.cancel()
  if self.watch:await self.watch.close()
  self.worker.cancel();await asyncio.gather(self.worker,return_exceptions=True)
  if self.output.process_frame is self.output_observed:self.output.process_frame=self.output_original
  self.core.close();self.records.clear()

def bind(processor,conversation,source_revision,stop_requested,*,core,core_sha256,call_held):
 return BackgroundDelivery(processor,conversation,source_revision,stop_requested,core=core,core_sha256=core_sha256,call_held=call_held)
