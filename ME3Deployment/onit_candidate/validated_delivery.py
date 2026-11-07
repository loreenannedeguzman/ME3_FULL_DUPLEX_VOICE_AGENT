"""Inactive new partial-delivery contract; never native-complete authority."""
import copy,hashlib,json,re,uuid
from dataclasses import dataclass
from onit_candidate.protocol import CandidateError,decode
from onit_candidate.answer_guard import clean_answer,substantive_answer,volume_claims,music_state_claims
DEFAULT_ENABLED=False
EARLY_INELIGIBLE=frozenset({'delivery_literal_requires_whole_final','delivery_incomplete_unit_boundary'})
# Fixed metadata only: never serialize arbitrary exception/model/tool text.
CALLBACK_CODES=frozenset({'delivery_dispatch_binding','delivery_provider_dispatch_binding','delivery_explicit_phase_required','delivery_provider_identity','delivery_provider_identity_changed','delivery_unsettled_tool_batch','delivery_pending_or_uncertain_review','delivery_sink_owner_changed','delivery_unit_sequence','delivery_unit_content','delivery_unit_bound','delivery_final_bound','delivery_interim_binding','delivery_model_sequence','delivery_header_once','delivery_unit_before_seal_or_after_end','delivery_end_bound','delivery_interim_kind','delivery_native_ack_mismatch','native_tool_markup_in_answer','unexplained_generic_refusal','unverified_volume_claim','ambiguous_music_state_claim','unverified_music_state_claim','unverified_music_readback_claim'})|EARLY_INELIGIBLE
def need(value,code):
 if not value:raise CandidateError(code)
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
def binding(value):
 need(type(value) is dict and set(value)=={'goal_id','engine_generation','owner_generation','stream_id'},'delivery_identity_shape')
 need(all(type(value[k]) is str and re.fullmatch('[a-f0-9]{32}',value[k]) for k in ('goal_id','stream_id')),'delivery_identity_hex')
 need(all(type(value[k]) is int and value[k]>0 for k in ('engine_generation','owner_generation')),'delivery_identity_generation')
 return dict(value)
def unit(value,index,*,early=False):
 need(type(value) is dict and set(value)=={'index','language','text'} and type(value['index']) is int and value['index']==index,'delivery_unit_sequence')
 need(value['language'] in ('en','es') and type(value['text']) is str and value['text'].strip() and '\x00' not in value['text'],'delivery_unit_content')
 value['text'].encode('utf-8','strict');need(len(value['text'])<=40000,'delivery_unit_bound')
 # Unclosed literal/markup content has no independently safe speech boundary.
 if early:
  need('```' not in value['text'] and '`' not in value['text'] and not re.search(r'<[^>]*$|\$[^$]*$',value['text']),'delivery_literal_requires_whole_final')
  need(value['text'].count('"')%2==0 and value['text'].count('“')==value['text'].count('”') and value['text'].count('**')%2==0 and not re.search(r'<[/A-Za-z][^>]*>|\\[([]',value['text']),'delivery_literal_requires_whole_final')
  need(re.search(r'''[.!?]["'”’)\]]*\s*$''',value['text']) is not None,'delivery_incomplete_unit_boundary')
 clean_answer(value['text']);substantive_answer(value['text']);return dict(value)
def parse_units(raw):
 value=decode(raw);need(type(value) is dict and list(value)==['phase','units','complete'] and value['phase'] in ('FINAL','WHOLE') and value['complete'] is True,'delivery_final_envelope')
 need(type(value['units']) is list and 1<=len(value['units'])<=32,'delivery_final_bound')
 rows=[unit(v,i) for i,v in enumerate(value['units'])];text=''.join(v['text'] for v in rows);need(len(text)<=40000,'delivery_final_bound')
 return text,[{'language':v['language'],'text':v['text']} for v in rows],rows
class ChildDelivery:
 """Trusted native-adapter acknowledgment while actual c.chat awaits model IPC."""
 def __init__(self,identity):self.identity=binding(identity);self.sealed=False;self.ended=False;self.rows=[];self.model_sequence=None
 def observe(self,sequence,event):
  need(type(event) is dict and set(event)=={'kind','binding','value'} and event['binding']==self.identity,'delivery_interim_binding')
  need(type(sequence) is int and sequence>0 and self.model_sequence in (None,sequence),'delivery_model_sequence')
  self.model_sequence=sequence;kind=event['kind'];value=event['value']
  if kind=='header':
   need(not self.sealed and value=={'phase':'FINAL'},'delivery_header_once');self.sealed=True
  elif kind=='unit':
   need(self.sealed and not self.ended and len(self.rows)<32,'delivery_unit_before_seal_or_after_end');row=unit(value,len(self.rows));self.rows.append(row)
   need(len(''.join(v['text'] for v in self.rows))<=40000,'delivery_final_bound')
  elif kind=='end':need(self.sealed and not self.ended and value=={'complete':True} and self.rows,'delivery_end_bound');self.ended=True
  else:raise CandidateError('delivery_interim_kind')
  return {'digest':digest(event),'units':len(self.rows),'sealed':self.sealed,'ended':self.ended,'native_complete':False}
 def terminal(self,result):
  message=result['choices'][0]['message']
  if not self.sealed:
   if message.get('tool_calls') or message.get('function_call'):return
   need(decode(message['content']).get('phase')=='WHOLE','delivery_final_without_seal');parse_units(message['content']);return
  need(not message.get('tool_calls') and not message.get('function_call'),'delivery_late_tool_forbidden')
  text,segments,rows=parse_units(message['content']);need(self.ended and rows==self.rows,'delivery_terminal_projection_mismatch')
@dataclass(frozen=True)
class UnitCommit:
 binding:object
 index:int
 language:str
 text:str
 digest:str
 issuer:object
 projection:str='source'
class HostDelivery:
 def __init__(self,engine,g,goal,sink):
  self.engine=engine;self.g=g;self.sink=sink;self.identity=binding(dict(goal_id=goal,engine_generation=g,owner_generation=sink.owner_generation,stream_id=uuid.uuid4().hex))
  need(callable(sink.current) and callable(sink.start) and callable(sink.commit) and callable(sink.terminal),'host_owned_delivery_sink_required')
  self.issuer=object();self.child=ChildDelivery(self.identity);self.commits=[];self.full_complete=False;self.deferred=False;self.defer_early=False;self.ordinal=None;self.native_sequence=None;self.provider_identity=None
  need(sink.start(dict(self.identity),self.issuer) is None,'delivery_sink_start_contract')
 def current(self):self.engine.check(self.g);need(self.sink.current(self.identity) is True,'delivery_sink_owner_changed')
 async def observe(self,event,process,sequence,ordinal,observations,batch_pending,expected_batch):
  try:return await self._observe(event,process,sequence,ordinal,observations,batch_pending,expected_batch)
  except Exception as error:
   code=str(error) if isinstance(error,CandidateError) and str(error) in CALLBACK_CODES else 'delivery_callback_rejected'
   events=getattr(self.engine,'events',None)
   if callable(getattr(events,'emit',None)):events.emit('validated_delivery_callback_rejected',generation=self.g,reason=code,committed_units=len(self.commits),text_retained=False)
   raise
 async def _observe(self,event,process,sequence,ordinal,observations,batch_pending,expected_batch):
  self.current();need(self.ordinal in (None,ordinal) and self.native_sequence in (None,sequence),'delivery_dispatch_binding');self.ordinal=ordinal;self.native_sequence=sequence
  need(event.get('goal_id')==self.identity['goal_id'] and event.get('reservation_ordinal')==ordinal,'delivery_provider_dispatch_binding')
  need(event.get('phase')=='FINAL','delivery_explicit_phase_required')
  identity=tuple(event.get(k) for k in ('response_id','item_id','output_index','content_index'))
  need(all(type(v) is str and 1<=len(v)<=256 for v in identity[:2]) and all(type(v) is int and 0<=v<64 for v in identity[2:]),'delivery_provider_identity')
  need(self.provider_identity in (None,identity),'delivery_provider_identity_changed');self.provider_identity=identity
  need(not batch_pending and not expected_batch and getattr(self.engine,'native_pending',None) is None,'delivery_unsettled_tool_batch')
  gateway=self.engine.gateway
  need(gateway is None or not gateway.blockers() and not gateway.actions.pending,'delivery_pending_or_uncertain_review')
  kind=event['kind'];value={'phase':'FINAL'} if kind=='header' else {'complete':True} if kind=='end' else {k:event[k] for k in ('index','language','text')}
  interim={'kind':kind,'binding':dict(self.identity),'value':value}
  if kind=='unit':
   # Full unit/claim validation is mandatory even if no early audio is possible.
   checked=unit(value,len(self.child.rows));prefix=''.join(v['text'] for v in self.child.rows)+checked['text']
   clean_answer(prefix);substantive_answer(prefix);volume_claims(prefix,observations);music_state_claims(prefix,observations)
   if not self.defer_early:
    try:unit(value,len(self.child.rows),early=True)
    except CandidateError as error:
     if str(error) not in EARLY_INELIGIBLE or self.commits:raise
     self.defer_early=True
     events=getattr(self.engine,'events',None)
     if callable(getattr(events,'emit',None)):events.emit('validated_delivery_early_deferred',generation=self.g,reason=str(error),committed_units=0,text_retained=False)
  expected=self.child.observe(sequence,interim)
  from onit_candidate.protocol import encode
  process.stdin.write(encode(dict(v=1,sequence=sequence,interim=interim)));await process.stdin.drain()
  import asyncio
  raw=await asyncio.wait_for(process.stdout.readline(),5);ack=decode(raw)
  need(ack==dict(v=1,sequence=sequence,kind='delivery_ack',ack=expected),'delivery_native_ack_mismatch');self.current()
  if kind=='unit' and not self.defer_early:
   receipt=UnitCommit(dict(self.identity),value['index'],value['language'],value['text'],digest(interim),self.issuer)
   self.commits.append(receipt);await self.sink.commit(receipt)
  return True
 async def complete(self,raw,answer,segments):
  self.current();text,rows,units=parse_units(raw)
  if decode(raw)['phase']=='WHOLE':
   need(not self.child.sealed and not self.commits and text==answer and rows==segments,'delivery_whole_after_commit')
   await self.sink.terminal('DEFERRED',dict(self.identity),(),answer,copy.deepcopy(segments));self.deferred=True;self.full_complete=True;return
  need(self.child.ended and units==self.child.rows and text==answer and rows==segments,'delivery_complete_projection')
  if self.defer_early:
   need(not self.commits,'delivery_deferred_after_commit')
   await self.sink.terminal('DEFERRED',dict(self.identity),(),answer,copy.deepcopy(segments));self.deferred=True;self.full_complete=True;return
  need(len(self.commits)==len(units),'delivery_complete_projection')
  await self.sink.terminal('FULL_COMPLETE',dict(self.identity),tuple(self.commits),answer,copy.deepcopy(segments));self.full_complete=True
 async def abort(self):
  if not self.full_complete:await self.sink.terminal('PARTIAL_FAILED' if self.commits else 'FAILED',dict(self.identity),tuple(self.commits),None,None)
