"""External same-process observation of the normal ME3 path, never admission.

Marks retain the actual Pi event time and use the course's existing websocket.
The binary pump flushes a bounded queue; no extra socket, worker or inference.
`audio_out` means first substantive PCM handed to the pipeline transport, not
physical audibility. Raw packet lineage and course audio retain wire timing.
"""
import asyncio,hashlib,json,math,os,time
from collections import deque
from pathlib import Path

def need(ok,code):
 if not ok:raise ValueError(code)

class StageObserver:
 def __init__(self,resource,events,*,maximum=4096):
  need(type(maximum) is int and 1<=maximum<=4096,'bounded_stage_queue_required')
  self.r=resource;self.bridge=resource.bridge;self.events=events;self.maximum=maximum
  self.queue=deque();self.rows=[];self.sent=0;self.closed=False;self.invalid=None
  self.serial=asyncio.Lock();self.requests=set();self.tokens=set();self.audio=set();self.vad=None
  models=resource.document.get('model_metadata')
  if models:self.queue.append(dict(event='describe',models=models))
  need(hasattr(events,'observer'),'actual_normal_event_observer_seam_required')
  self.original_observer=events.observer;self.original_pcm=self.bridge.core.send_pcm
  self.clients=resource.processor.controller.clients;self.original_call=self.clients.call
  # Transport captured events.emit before pipeline assembly. The actual normal
  # Events.observer slot is read dynamically by that captured method as well.
  def observe(event,**fields):
   if self.original_observer:self.original_observer(event,**fields)
   self.event(event,fields)
  self.observer_hook=observe;events.observer=observe
  async def call(role,generation,**arguments):
   value=await self.original_call(role,generation,**arguments)
   if role=='asr' and self.current(generation):
    need(type(value) is dict and type(value.get('text')) is str,'actual_asr_transcript_required')
    if value['text'].strip():
     self.mark('asr_final',generation=generation,text=value['text'],request_id=value.get('request_id'),
      observation='accepted_final_after_actual_Proxy_recognize_segment_resolution')
   return value
  self.call_hook=call;self.clients.call=call
  async def pcm(data):
   await self.original_pcm(data)
   if self.bridge.ready.is_set():await self.flush()
  self.pcm_hook=pcm;self.bridge.core.send_pcm=pcm
 def current(self,g):
  c=self.r.processor.controller
  return not self.closed and type(g) is int and g>0 and c.clock.current==g and not c.closed
 def mark(self,stage,*,t=None,**fields):
  if self.closed:return
  t=time.monotonic() if t is None else t
  need(type(t) in (int,float) and math.isfinite(t),'finite_same_process_stage_time_required')
  if len(self.queue)>=self.maximum or len(self.rows)>=100000:
   self.invalid='stage_observation_overflow';raise ValueError(self.invalid)
  row=dict(event='mark',stage=stage,t=t,pid=os.getpid(),**fields)
  # Exact transcript retained; unlike fdbench_marks.mark this does not truncate.
  need(len(json.dumps(row,ensure_ascii=True))<=32768,'bounded_stage_payload_required')
  self.rows.append(row);self.queue.append(row)
 def event(self,event,f):
  if self.closed or not self.bridge.ready.is_set():return # greeting is outside trials
  g=f.get('generation');now=f.get('software_ns') or f.get('monotonic_ns')
  t=now/1e9 if type(now) is int else time.monotonic()
  if event=='digital_vad_state':
   state=f.get('state')
   if state=='SPEAKING' and self.vad!='SPEAKING':self.mark('vad_start',t=t,generation=g)
   if state=='QUIET' and self.vad in {'SPEAKING','STOPPING'}:self.mark('vad_end',t=t,generation=g)
   self.vad=state
  elif event=='openai_plan_request_dispatch':
   ordinal=f.get('ordinal');need(type(ordinal) is int and ordinal>0,'actual_model_ordinal_required')
   need(ordinal not in self.requests,'duplicate_model_request_observation')
   self.requests.add(ordinal);self.mark('llm_request',t=t,ordinal=ordinal,speculative=False,model=f.get('model'))
  elif event=='openai_plan_stage_timing' and f.get('stage')=='first_output_text_delta_received':
   ordinal=f.get('ordinal');need(ordinal in self.requests,'first_token_without_request')
   if ordinal not in self.tokens:self.tokens.add(ordinal);self.mark('llm_first_token',t=t,ordinal=ordinal)
  elif event=='openai_plan_request_completed':
   usage=f.get('usage') or {}
   self.mark('llm_usage',t=t,ordinal=f.get('ordinal'),**{k:v for k,v in usage.items() if k in {'prompt_tokens','completion_tokens','total_tokens'} and type(v) is int})
  elif event=='openai_plan_request_failed' and f.get('error_code')=='request_interrupted':
   self.mark('llm_cancel',t=t,ordinal=f.get('ordinal'))
  elif event=='mcp_request_start' and f.get('role')=='tts' and self.current(g):
   self.mark('tts_request',t=t,generation=g,request_id=f.get('request_id'))
  elif event=='mcp_request_end' and f.get('role')=='tts' and self.current(g) and f.get('result_bytes',0)>0:
   self.mark('tts_first_audio',t=t,generation=g,request_id=f.get('request_id'),observation='complete_nonstreamed_MCP_PCM_result')
  elif event=='generation_playout_started' and f.get('kind')=='response' and self.current(g) and g not in self.audio:
   self.audio.add(g);self.mark('audio_out',t=t,generation=g,observation='first_substantive_PCM_handed_to_pipeline_transport_not_audible')
  elif event=='digital_output_clear':self.mark('interrupted',t=t,epoch=f.get('epoch'))
  elif event=='digital_media_tail_drained' and type(g) is int and g>0:self.mark('bot_stopped',t=t,generation=g,epoch=f.get('epoch'))
  elif event in {'assistant_rpc_start','assistant_rpc_end'}:
   self.mark('tool_start' if event.endswith('_start') else 'tool_end',t=t,name=str(f.get('server'))+'__'+str(f.get('tool')),call_id=f.get('request_id'))
 async def flush(self):
  async with self.serial:
   while self.queue:
    need(not self.bridge.write_blocked,'stage_wire_write_fenced')
    row=self.queue[0];await self.bridge.core.send_control(row)
    self.queue.popleft();self.sent+=1
 def finish(self):
  self.closed=True
  if self.events.observer is self.observer_hook:self.events.observer=self.original_observer
  if self.clients.call is self.call_hook:self.clients.call=self.original_call
  if self.bridge.core.send_pcm is self.pcm_hook:self.bridge.core.send_pcm=self.original_pcm
  p=Path(self.r.log_dir)/'stage-observation.json'
  value=dict(version=1,source_revision=self.r.document['source_revision'],contract_sha256=self.r.sha256,
   clock='assistant_process_monotonic_seconds',marks=self.rows,sent=self.sent,pending=len(self.queue),invalid=self.invalid,
   complete=self.invalid is None and not self.queue,physical_audio=False,automatic_pipecat_observer=False,
   observer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
  with p.open('x',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=True);f.write('\n')
  need(value['complete'],'stage_observation_incomplete')

def attach(resource,events):
 need(getattr(resource,'stage_observer',None) is None,'single_stage_observer_required')
 resource.stage_observer=StageObserver(resource,events)
