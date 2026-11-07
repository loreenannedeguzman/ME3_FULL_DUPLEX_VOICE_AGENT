"""Deterministic permission/action/verification gateway; never a task planner."""
import asyncio,copy,hashlib,json,os,re,stat,time
from pathlib import Path
from jsonschema import Draft202012Validator
from me3_assistant.actions import Actions
from me3_assistant.guard import lock
from task_mcp.reasoner import CONFIRM,latest_text,normalized
from onit_candidate.protocol import CandidateError
from onit_candidate.confirmation import confirmation_intent
MESSAGE_ACTIONS={'propose_whatsapp_message','propose_contact_message'}
GMAIL_ACTION='propose_gmail_send'
GMAIL_READ_ACTION='propose_gmail_read'
STATES={'LOCAL_ENDED','HANDOFF_AUTHORIZED','REQUESTED','WAITING_CONFIRMATION','VERIFIED','UNCERTAIN','CANCELED','NOT_DISPATCHED','SESSION_ENDED','OWNER_REPORTED_COMPLETE','RETIRED_UNCERTAIN','READ_UNVERIFIED','REJECTED_BEFORE_SEND'}
def fingerprint(name,args):return hashlib.sha256(json.dumps([name,args],sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
# Public single-command views of one existing private guarded action.
# No new action authority, planner, guessed arguments or effect retry.
STREAM_VIEWS={
 'soundcloud_play_track':('play',['selection_id','volume'],['selection_id'],'Play the exact SoundCloud selection ID returned by search, optionally at player volume0â€“100. Playback requires independent outcome verification.'),
 'soundcloud_set_volume':('volume',['volume'],['volume'],'Set absolute SoundCloud player volume to an integer percentage0â€“100. Use the requested valid value; out-of-range or uncertain values need clarification.'),
 'soundcloud_pause':('pause',[],[],'Pause current SoundCloud playback with independent outcome verification.'),
 'soundcloud_resume':('resume',[],[],'Resume current SoundCloud playback with independent outcome verification.'),
 'soundcloud_stop':('stop',[],[],'Stop current SoundCloud music while keeping the conversation available. Outcome is independently verified.')}
def stream_views(declarations):
 result=[]
 for declaration in declarations:
  if declaration['name']!='propose_stream_control':result.append(copy.deepcopy(declaration));continue
  original=declaration['parameters'];commands=set(original['properties']['command']['enum'])
  for restriction in original.get('allOf',[]):
   values=restriction.get('properties',{}).get('command',{}).get('enum')
   if isinstance(values,list):commands.intersection_update(values)
  for name,(command,fields,required,description) in STREAM_VIEWS.items():
   if command not in commands:continue
   schema=dict(type='object',properties={field:copy.deepcopy(original['properties'][field]) for field in fields},required=required,additionalProperties=False)
   for restriction in original.get('allOf',[]):
    scoped=copy.deepcopy(restriction);scoped.get('properties',{}).pop('command',None)
    if scoped.get('properties'):schema.setdefault('allOf',[]).append(scoped)
   result.append(dict(name=name,description=description,parameters=schema))
 return result
def stream_call(name,args):
 if name not in STREAM_VIEWS:return name,args
 return 'propose_stream_control',dict(command=STREAM_VIEWS[name][0],**args)

class Ledger:
 def __init__(self,path,root):
  self.path=Path(path);root=Path(root).resolve();self.root=root
  if not any(self.path.resolve().is_relative_to(root/p) for p in ['data','.tmp']):raise CandidateError('gateway_ledger_outside_project')
  self.rows={};self.sequence=0;self.load()
 def trusted(self):
  if self.path.is_symlink():raise CandidateError('gateway_ledger_untrusted')
  if self.path.exists():
   s=self.path.stat()
   if not stat.S_ISREG(s.st_mode) or s.st_nlink!=1 or s.st_size>2000000:raise CandidateError('gateway_ledger_untrusted')
 def load(self):
  self.trusted();rows={};sequence=0
  for line in (self.path.read_text().splitlines() if self.path.exists() else []):
   row=json.loads(line);sequence+=1
   if (row.get('sequence')!=sequence or row.get('state') not in STATES or not re.fullmatch('[0-9a-f]{32}',row.get('goal_id','')) or not re.fullmatch('[0-9a-f]{64}',row.get('intent_key','')) or not re.fullmatch('[a-z][a-z0-9_]{0,63}',row.get('action','')) or row.get('operation_id') is not None and not re.fullmatch('[0-9a-f]{32}',row['operation_id'])):raise CandidateError('gateway_ledger_invalid')
   if row['state']=='LOCAL_ENDED' and row['action']!='propose_whatsapp_call':raise CandidateError('local_call_terminal_scope_required')
   key=(row['goal_id'],row['intent_key']);old=rows.get(key)
   if row['state']=='READ_UNVERIFIED' and (row['action']!=GMAIL_READ_ACTION or not old or old['state'] not in {'WAITING_CONFIRMATION','REQUESTED','UNCERTAIN'}):raise CandidateError('prior_confirmed_gmail_read_required')
   if row['state']=='LOCAL_ENDED' and (not old or old['state'] not in {'HANDOFF_AUTHORIZED','REQUESTED','UNCERTAIN','LOCAL_ENDED'}):raise CandidateError('prior_owned_call_required_for_local_end')
   if row['state']=='HANDOFF_AUTHORIZED' and (row['action']!='propose_whatsapp_call' or not old or old['state']!='WAITING_CONFIRMATION'):raise CandidateError('fresh_call_review_handoff_required')
   if old and (old['action']!=row['action'] or old.get('operation_id') and old['operation_id']!=row.get('operation_id')):raise CandidateError('gateway_ledger_identity_changed')
   if old and old['state']=='REJECTED_BEFORE_SEND':raise CandidateError('rejected_intent_cannot_replay')
   if row['state']=='REJECTED_BEFORE_SEND':
    from me3_whatsapp.contact_outbound import validate_pre_send_rejection
    proof=row.get('pre_send_rejection')
    if row['action']!='propose_contact_message' or not old or old['state']!='UNCERTAIN' or old.get('action')!=row['action'] or old.get('operation_id') is None or old.get('operation_id')!=row['operation_id']:raise CandidateError('prior_uncertain_contact_required')
    validate_pre_send_rejection(self.root,proof,row['operation_id'],proof.get('expected_digest') if isinstance(proof,dict) else None)
    from me3_assistant.actions import Journal
    host=Journal(self.root/'data/assistant-actions.jsonl').records.get(row['operation_id'],{})
    if host.get('state')!='REJECTED_BEFORE_SEND' or host.get('pre_send_rejection')!=proof or host.get('expected_digest')!=proof['expected_digest'] or host.get('action')!=row['action']:raise CandidateError('terminal_host_rejection_required')
   elif row.get('pre_send_rejection') is not None:raise CandidateError('rejection_terminal_state_required')
   correction=row.get('owner_report_correction')
   if old and old['state']=='RETIRED_UNCERTAIN':raise CandidateError('retired_unknown_operation_cannot_replay')
   if row['state']=='RETIRED_UNCERTAIN':
    from me3_assistant.owner_reports import verify_retirement
    if not old or old['state']!='UNCERTAIN':raise CandidateError('prior_corrected_uncertainty_required')
    verify_retirement(self.root,row.get('uncertain_retirement'),old.get('owner_report_correction'),row['operation_id'],row['action'])
   elif row.get('uncertain_retirement') is not None:raise CandidateError('retirement_state_required')
   if old and old['state']=='OWNER_REPORTED_COMPLETE' and row['state']!='OWNER_REPORTED_COMPLETE':
    from me3_assistant.owner_reports import verify_correction
    if row['state']!='UNCERTAIN':raise CandidateError('owner_reported_operation_cannot_replay')
    verify_correction(self.root,correction,old.get('owner_report'),row['operation_id'],row['action'])
   elif correction is not None:
    if not old or correction!=old.get('owner_report_correction') or row['state'] not in {'UNCERTAIN','VERIFIED','RETIRED_UNCERTAIN'}:raise CandidateError('owner_correction_cannot_replay')
   if old and old.get('owner_report_correction') and (correction!=old['owner_report_correction'] or row['state'] not in {'UNCERTAIN','VERIFIED','RETIRED_UNCERTAIN'}):raise CandidateError('owner_correction_cannot_replay')
   if row['state']=='OWNER_REPORTED_COMPLETE':
    from me3_assistant.owner_reports import verify
    if not old or old['state'] not in {'UNCERTAIN','REQUESTED','OWNER_REPORTED_COMPLETE'}:raise CandidateError('owner_report_requires_uncertain_operation')
    verify(self.root,row.get('owner_report'),row['operation_id'],row['action'])
   rows[key]=row
  self.rows=rows;self.sequence=sequence;return rows
 def record(self,goal,key,action,state,operation_id=None,*,owner_report=None,owner_report_correction=None,uncertain_retirement=None,pre_send_rejection=None):
  self.path.parent.mkdir(parents=True,exist_ok=True)
  with lock(self.path.with_suffix('.lock')):
   self.load();old=self.rows.get((goal,key))
   if old and old.get('operation_id'):
    if operation_id is not None and operation_id!=old['operation_id']:raise CandidateError('gateway_ledger_identity_changed')
    operation_id=old['operation_id']
   if old and old['state']=='REJECTED_BEFORE_SEND':raise CandidateError('rejected_intent_cannot_replay')
   if state=='REJECTED_BEFORE_SEND':
    from me3_whatsapp.contact_outbound import validate_pre_send_rejection
    if action!='propose_contact_message' or not old or old['state']!='UNCERTAIN' or old.get('action')!=action or old.get('operation_id') is None or old.get('operation_id')!=operation_id:raise CandidateError('prior_uncertain_contact_required')
    validate_pre_send_rejection(self.root,pre_send_rejection,operation_id,pre_send_rejection.get('expected_digest') if isinstance(pre_send_rejection,dict) else None);row_proof=copy.deepcopy(pre_send_rejection)
    from me3_assistant.actions import Journal
    host=Journal(self.root/'data/assistant-actions.jsonl').records.get(operation_id,{})
    if host.get('state')!='REJECTED_BEFORE_SEND' or host.get('pre_send_rejection')!=pre_send_rejection or host.get('expected_digest')!=pre_send_rejection['expected_digest'] or host.get('action')!=action:raise CandidateError('terminal_host_rejection_required')
   elif pre_send_rejection is not None:raise CandidateError('rejection_terminal_state_required')
   if state=='HANDOFF_AUTHORIZED' and (action!='propose_whatsapp_call' or not old or old['state']!='WAITING_CONFIRMATION'):raise CandidateError('fresh_call_review_handoff_required')
   if state=='LOCAL_ENDED' and (action!='propose_whatsapp_call' or not old or old['state'] not in {'HANDOFF_AUTHORIZED','REQUESTED','UNCERTAIN','LOCAL_ENDED'}):raise CandidateError('prior_owned_call_required_for_local_end')
   row=dict(sequence=self.sequence+1,goal_id=goal,intent_key=key,action=action,state=state,operation_id=operation_id,at=time.time())
   if state=='REJECTED_BEFORE_SEND':row['pre_send_rejection']=row_proof
   if old and old['state']=='RETIRED_UNCERTAIN':raise CandidateError('retired_unknown_operation_cannot_replay')
   if state=='RETIRED_UNCERTAIN':
    from me3_assistant.owner_reports import verify_retirement
    if not old or old['state']!='UNCERTAIN':raise CandidateError('prior_corrected_uncertainty_required')
    verify_retirement(self.root,uncertain_retirement,old.get('owner_report_correction'),operation_id,action);row['uncertain_retirement']=uncertain_retirement
   elif uncertain_retirement is not None:raise CandidateError('retirement_state_required')
   if old and old['state']=='OWNER_REPORTED_COMPLETE':
    from me3_assistant.owner_reports import verify_correction
    if state!='UNCERTAIN':raise CandidateError('owner_reported_operation_cannot_replay')
    verify_correction(self.root,owner_report_correction,old.get('owner_report'),operation_id,action);row['owner_report_correction']=owner_report_correction
   elif old and old.get('owner_report_correction'):
    if state not in {'UNCERTAIN','VERIFIED','RETIRED_UNCERTAIN'} or owner_report_correction is not None and owner_report_correction!=old['owner_report_correction']:raise CandidateError('owner_correction_cannot_replay')
    row['owner_report_correction']=old['owner_report_correction']
   elif owner_report_correction is not None:raise CandidateError('prior_owner_report_required')
   if state=='OWNER_REPORTED_COMPLETE':
    from me3_assistant.owner_reports import verify
    if not old or old['state'] not in {'UNCERTAIN','REQUESTED'}:raise CandidateError('owner_report_requires_uncertain_operation')
    verify(self.root,owner_report,operation_id,action);row['owner_report']=owner_report
   elif owner_report is not None:raise CandidateError('owner_report_state_required')
   fd=os.open(self.path,os.O_WRONLY|os.O_APPEND|os.O_CREAT|getattr(os,'O_NOFOLLOW',0),0o600)
   with os.fdopen(fd,'w',encoding='utf-8') as f:f.write(json.dumps(row,separators=(',',':'))+'\n');f.flush();os.fsync(f.fileno())
   self.sequence+=1;self.rows[(goal,key)]=row;return row
class ActionReadbackRequired(CandidateError):
 """Host-owned refusal after structural batch validation; never action authority."""
 def __init__(self,gateway,calls):
  self.gateway=gateway;self.generation=gateway.generation;self.calls=copy.deepcopy(calls)
  super().__init__('gateway_uncertain_outcome_requires_readback')
 def matches(self,gateway,g,calls):
  return self.gateway is gateway and self.generation==g and self.calls==calls

class _WindowCleanupPermit:
 """One host-created Actions handoff; never a tool argument or reusable grant."""
 def __init__(self,gate,calls,g,key):
  self.gate=gate;self.issuer=gate._cleanup_issuer;self.calls=copy.deepcopy(calls);self.g=g;self.key=key
  self.goal=gate.goal;self.request=gate.actions.request_text;self.owner=gate.registry.communication_owner
  self.grant_digest=fingerprint('window_cleanup_grant',gate.registry.session_grant);self.closed=False;self.used=False
 def consume(self,actions,name,args,g):
  gate=self.gate
  if (self.closed or self.used or self.issuer is not gate._cleanup_issuer
      or getattr(gate,'_uncertain_cleanup_permit',None) is not self
      or getattr(actions,'_uncertain_cleanup_permit',None) is not self or gate.actions is not actions
      or g!=self.g or gate.goal!=self.goal or actions.request_text!=self.request
      or gate.registry.communication_owner!=self.owner
      or fingerprint('window_cleanup_grant',gate.registry.session_grant)!=self.grant_digest
      or self.calls!=[dict(name=name,args=args)]):return False
  if not gate.window_cleanup_allowed(self.calls,g,own_request=self.key):return False
  self.used=True;return True

class Gateway:
 def __init__(self,root,registry,budget,journal,events,current,ledger_path,*,action_scope=None):
  self._cleanup_issuer=object();self._uncertain_cleanup_permit=None
  self._cleanup_source=getattr(registry,'session_grant',{}).get('source_revision');self._cleanup_owner=getattr(registry,'communication_owner',None)
  self.registry=registry;self.journal=journal;self.events=events;self.current=current;self.ledger=Ledger(ledger_path,root);self.goal=None;self.generation=None;self.pending_key=None;self.deferred_intents={}
  grant=getattr(registry,'session_grant',{});allowed=set(getattr(registry,'allowed_actions',[]))&set(grant.get('allowed_actions',[]))
  self.actions=Actions(registry,registry.config['actions'],budget,journal,events,current,allowed=allowed)
  self.scope=copy.deepcopy(action_scope or {})
  if not set(self.scope)<=allowed:raise CandidateError('gateway_scope_not_authorized')
  self.action_declarations=[]
  for d in self.actions.declarations():
   d=copy.deepcopy(d)
   for field,values in self.scope.get(d['name'],{}).items():
    prop=d['parameters'].get('properties',{}).get(field)
    if prop is None or not isinstance(values,list) or not values:raise CandidateError('gateway_invalid_scope')
    # Additional restriction; never replace the original value constraints.
    d['parameters'].setdefault('allOf',[]).append({'properties':{field:{'enum':values}}})
   self.action_declarations.append(d)
  self.canonical_action_declarations={d['name']:copy.deepcopy(d) for d in self.action_declarations}
  self.action_declarations=stream_views(self.action_declarations)
 def observation_tool(self,name):return 'propose_stream_control' if name in STREAM_VIEWS else name
 def activate(self,goal,g,*,request=None,preserve_review=False):
  if not re.fullmatch('[0-9a-f]{32}',goal):raise CandidateError('gateway_invalid_goal_id')
  if preserve_review and self.actions.pending:
   if not self.pending_key or self.pending_key[0]!=goal:raise CandidateError('pending_review_goal_changed')
  elif self.actions.pending:self.cancel_pending()
  if request is not None and (not isinstance(request,str) or len(request)>4000):raise CandidateError('gateway_invalid_request_boundary')
  self.actions.request_text=request
  self.goal=goal;self.generation=g;self.ledger.load()
 def declarations(self):
  controls=[dict(name='check_last_action',description='Independently reconcile unresolved action outcomes without repeating a write. Does not inspect contacts or re-query all historical completed actions. Run alone before another action when an outcome is uncertain.',parameters={'type':'object','properties':{},'additionalProperties':False}),dict(name='cancel_pending_action',description='Cancel an uncommitted proposal only; keep conversation and independently verified selected contact. Choose mode revise to cancel the old review and ask for replacement content. Choose discard to cancel only (default). Choose continue only when a different substantive goal in the latest request must still be planned after cancellation. These are non-executing outcomes; committed effects remain unchanged.',parameters={'type':'object','properties':{'mode':{'type':'string','enum':['discard','revise','continue']}},'additionalProperties':False})]
  controls.append(dict(name='repeat_pending_review',description='Repeat the unchanged pending host review after unclear speech or a request to hear it again. Never confirms, sends or creates another operation. For a substantive replacement, cancel the obsolete review before answering or propose the revised action for NEW exact review.',parameters={'type':'object','properties':{},'additionalProperties':False}))
  preview=getattr(self,'preview_accept_declaration',lambda:None)()
  if preview is not None:controls.append(preview)
  else:
   if callable(getattr(self,'artifact_preview_offer',None)):
    from onit_candidate.artifact_offer import declaration
    controls.append(declaration())
   if callable(getattr(self,'runtime_preview_offer',None)):
    from onit_candidate.runtime_view import declaration as runtime_declaration
    controls.append(runtime_declaration())
  if callable(getattr(self,'service_monitor',None)):
   from onit_candidate.service_monitor import declaration as monitor_declaration
   controls.append(monitor_declaration())
  close=getattr(self,'preview_close_declaration',lambda:None)()
  if close is not None:controls.append(close)
  return copy.deepcopy(self.action_declarations)+controls
 def blockers(self):
  self.ledger.load()
  return bool(self.journal.unresolved() or any(v['state'] in {'HANDOFF_AUTHORIZED','REQUESTED','UNCERTAIN'} for v in self.ledger.rows.values()))
 def window_cleanup_allowed(self,calls,g,*,own_request=None):
  # Pure exact-current cleanup only. All ordinary effects retain blockers().
  if (not isinstance(calls,list) or len(calls)!=1 or not isinstance(calls[0],dict)
      or not {'name','args'}<=set(calls[0])<= {'id','name','args'}
      or calls[0]['name']!='propose_whatsapp_window' or not isinstance(calls[0]['args'],dict) or calls[0]['args']!={}
      or 'id' in calls[0] and (not isinstance(calls[0]['id'],str) or not re.fullmatch('[A-Za-z0-9_-]{1,128}',calls[0]['id']))):return False
  from me3_whatsapp.window_action import WhatsAppWindow
  from me3_whatsapp.window_budget import authorized
  from me3_whatsapp.window import command_allowed
  self.check(g);registry=self.registry;actions=self.actions;grant=getattr(registry,'session_grant',{})
  adapter=actions.adapters.get('propose_whatsapp_window');context=getattr(registry,'communication_context',None)
  owner=getattr(registry,'communication_owner',None);request=actions.request_text
  if (actions.pending is not None or actions.registry is not registry
      or type(adapter) is not WhatsAppWindow or adapter.registry is not registry
      or adapter.budget_role!='window_control' or 'propose_whatsapp_window' not in actions.allowed
      or not authorized(grant) or grant.get('approved') is not True
      or not isinstance(grant.get('source_revision'),str) or not re.fullmatch('[0-9a-f]{64}',grant['source_revision'])
      or grant['source_revision']!=self._cleanup_source or owner!=self._cleanup_owner
      or not isinstance(owner,str) or not re.fullmatch('[0-9a-f]{32}',owner)
      or not isinstance(context,dict) or context.get('goal')!=self.goal or context.get('generation')!=g
      or context.get('request')!=request or not command_allowed(request)
      or registry.current(g) is not True):return False
  session=registry.sessions.get(adapter.server)
  if (not session or session[1].get('module')!='me3_whatsapp.server' or session[1].get('identity')!='me3'
      or any((adapter.server,n) not in registry.schemas for n in {'prepare_window_close','commit_window_close','read_window_operation'})
      or {'prepare_window_close','commit_window_close','read_window_operation'}&set(session[1].get('read_tools',[]))):return False
  journal={r['operation_id']:r for r in self.journal.unresolved()};rows=[];owned_found=own_request is None
  for key,row in self.ledger.load().items():
   if row['state'] not in {'HANDOFF_AUTHORIZED','REQUESTED','UNCERTAIN'}:continue
   if own_request is not None and key==own_request:
    if (key!=(self.goal,fingerprint('propose_whatsapp_window',{})) or row['action']!='propose_whatsapp_window'
        or row['state']!='REQUESTED' or row.get('operation_id') is not None):return False
    owned_found=True;continue
   rows.append(row)
  if (not owned_found or not journal or not rows or len(rows)!=len(journal)
      or any(r.get('action')!='propose_contact_message' or r.get('state')!='UNCERTAIN' for r in journal.values())
      or any(r.get('action')!='propose_contact_message' or r.get('state')!='UNCERTAIN'
             or r.get('operation_id') not in journal for r in rows)
      or {r.get('operation_id') for r in rows}!=set(journal)):return False
  return True
 def validate_batch(self,calls,schemas):
  seen=set();has_action=False
  if not isinstance(calls,list) or not 1<=len(calls)<=self.registry.config['runtime']['batch_calls']:raise CandidateError('gateway_batch_limit')
  actions={d['name'] for d in self.action_declarations};controls={'check_last_action','cancel_pending_action','repeat_pending_review'}
  if callable(getattr(self,'artifact_preview_offer',None)):
   controls.add('offer_artifact_preview')
   if any(c.get('name')=='offer_artifact_preview' for c in calls) and len(calls)!=1:raise CandidateError('artifact_preview_offer_must_be_alone')
  if callable(getattr(self,'runtime_preview_offer',None)):
   controls.add('offer_runtime_preview')
   if any(c.get('name')=='offer_runtime_preview' for c in calls) and len(calls)!=1:raise CandidateError('runtime_preview_offer_must_be_alone')
  if getattr(self,'preview_accept_declaration',lambda:None)() is not None:
   controls.add('accept_pending_preview')
   if any(c.get('name')=='accept_pending_preview' for c in calls) and len(calls)!=1:raise CandidateError('preview_acceptance_must_be_alone')
  if getattr(self,'preview_close_declaration',lambda:None)() is not None:
   controls.add('close_preview')
   if any(c.get('name')=='close_preview' for c in calls) and len(calls)!=1:raise CandidateError('preview_close_must_be_alone')
  if callable(getattr(self,'service_monitor',None)):
   controls.add('manage_service_monitor')
   if any(c.get('name')=='manage_service_monitor' for c in calls) and len(calls)!=1:raise CandidateError('service_monitor_must_be_alone')
  for c in calls:
   name=c.get('name');args=c.get('args')
   if name not in schemas or name not in (set(self.registry.reads)|actions|controls) or (name in self.registry.workspace_operations and getattr(self.registry,'session_grant',{}).get('workspace_access') is not True):raise CandidateError('gateway_operation_not_permitted')
   large_write=(name=='workspace__write_workspace_file' and getattr(self.registry,'workspace_write_feedback_contract',lambda alias:False)(name))
   maximum=400000 if large_write else 12000
   if not isinstance(args,dict) or len(json.dumps(args,allow_nan=False))>maximum:raise CandidateError('gateway_arguments_invalid')
   if getattr(self.registry,'productivity_read_contract',lambda alias:False)(name):self.registry.productivity_read_preflight(name,args)
   else:Draft202012Validator(schemas[name]).validate(args)
   if name in actions:
    canonical,arguments=stream_call(name,args);has_action=True;key=fingerprint(canonical,arguments)
    if key in seen:raise CandidateError('gateway_duplicate_batch_effect')
    seen.add(key)
  if has_action and self.blockers() and not self.window_cleanup_allowed(calls,self.generation):raise ActionReadbackRequired(self,calls)
 def check(self,g):
  if self.goal is None or g!=self.generation or not self.current(g):raise asyncio.CancelledError()
 def progress(self,state,action,g):self.events.emit('onit_gateway_progress',generation=g,goal_id=self.goal,state=state,action=action)
 def cancel_pending(self):
  p=self.actions.pending;self.actions.cancel()
  if p and self.pending_key:
   row=self.ledger.rows[self.pending_key];self.ledger.record(row['goal_id'],row['intent_key'],row['action'],'CANCELED',p['operation_id'])
  self.pending_key=None
 async def verify_record(self,record,g):
  adapter=self.actions.adapters.get(record.get('action'))
  if adapter is None:return False
  try:return bool(await adapter.verify(record,g))
  except asyncio.CancelledError:raise
  except Exception:return False
 async def reconcile_contact_rejection(self,record,g):
  if record.get('action')!='propose_contact_message' or record.get('state') not in {'UNCERTAIN','REJECTED_BEFORE_SEND'}:return None
  from me3_whatsapp.contact_message_action import ContactMessage
  from me3_whatsapp.contact_outbound import validate_pre_send_rejection
  before=copy.deepcopy(record);op=record['operation_id']
  if record['state']=='REJECTED_BEFORE_SEND':outcome=dict(verified=False,pre_send_rejection=record.get('pre_send_rejection'))
  else:
   adapter=self.actions.adapters.get(record['action'])
   if not isinstance(adapter,ContactMessage):return None
   outcome=await adapter.inspect_outcome(copy.deepcopy(record),g);self.check(g)
  if self.journal.records.get(op)!=before:raise CandidateError('reconciliation_operation_changed')
  proof=outcome.get('pre_send_rejection')
  if proof is not None:
   validate_pre_send_rejection(self.ledger.root,proof,op,record['expected_digest']);self.check(g)
   if before['state']=='UNCERTAIN':self.journal.record(op,record['action'],'REJECTED_BEFORE_SEND',record['expected_digest'],pre_send_rejection=proof)
   return dict(verified=False,pre_send_rejection=proof)
  return outcome
 async def status(self,g):
  self.check(g);self.ledger.load();results=[]
  # Resolve every known uncertain identity, rather than just the last row.
  for row in list(self.ledger.rows.values()):
   op=row.get('operation_id');record=self.journal.records.get(op,{})
   if row['state'] in {'HANDOFF_AUTHORIZED','REQUESTED','UNCERTAIN'}:
    if record.get('state')=='SESSION_ENDED':
     self.ledger.record(row['goal_id'],row['intent_key'],row['action'],'SESSION_ENDED',op);results.append(dict(action=row['action'],status='session_ended_original_outcome_unverified'));continue
    outcome=await self.reconcile_contact_rejection(record,g) if record else None
    if outcome is not None and outcome.get('pre_send_rejection') is not None:
     self.ledger.record(row['goal_id'],row['intent_key'],row['action'],'REJECTED_BEFORE_SEND',op,pre_send_rejection=outcome['pre_send_rejection']);results.append(dict(action=row['action'],status='rejected_before_send',send_started=False,old_operation_reusable=False));continue
    verified=outcome.get('verified') is True if outcome is not None else bool(record) and await self.verify_record(record,g);self.check(g)
    if verified:
     self.journal.record(op,row['action'],'VERIFIED',record['expected_digest']);self.ledger.record(row['goal_id'],row['intent_key'],row['action'],'VERIFIED',op)
    results.append(dict(action=row['action'],status='independently_verified' if verified else 'unverified'))
  # Journal rows may predate this candidate ledger. Keep them guarded, and
  # allow a read-only reconciliation without replay or model-selected IDs.
  linked={v.get('operation_id') for v in self.ledger.rows.values()}
  for record in list(self.journal.unresolved()):
   if record['operation_id'] in linked:continue
   outcome=await self.reconcile_contact_rejection(record,g)
   if outcome is not None and outcome.get('pre_send_rejection') is not None:
    results.append(dict(action=record['action'],status='rejected_before_send',send_started=False,old_operation_reusable=False));continue
   verified=outcome.get('verified') is True if outcome is not None else await self.verify_record(record,g);self.check(g)
   if verified:self.journal.record(record['operation_id'],record['action'],'VERIFIED',record['expected_digest'])
   results.append(dict(action=record['action'],status='independently_verified' if verified else 'unverified'))
  return dict(status=('no_unresolved_action' if self.ledger.rows else 'no_previous_action') if not results else 'unverified' if any(r['status']=='unverified' for r in results) else 'checked',actions=results,write_retried=False,completed_actions_undone=False)
 async def execute(self,name,args,g):
  self.check(g)
  declarations={d['name']:d for d in self.registry.declarations()+self.declarations()}
  if name not in declarations:raise CandidateError('gateway_operation_not_permitted')
  if getattr(self.registry,'productivity_read_contract',lambda alias:False)(name):self.registry.productivity_read_preflight(name,args)
  else:Draft202012Validator(declarations[name]['parameters']).validate(args)
  if name=='manage_service_monitor':
   if self.actions.pending or self.blockers():raise CandidateError('service_monitor_waits_for_action_resolution')
   return await self.service_monitor(args,g)
  if name=='close_preview':
   if self.actions.pending or self.blockers():raise CandidateError('preview_close_waits_for_action_resolution')
   return await self.close_preview(args,g)
  if name=='accept_pending_preview':
   if self.actions.pending or self.blockers():raise CandidateError('preview_acceptance_waits_for_action_resolution')
   return await self.preview_accept(args,g)
  if name=='offer_runtime_preview':
   if self.actions.pending or self.blockers():raise CandidateError('runtime_preview_waits_for_action_resolution')
   return await self.runtime_preview_offer(args,g)
  if name=='offer_artifact_preview':
   if self.actions.pending or self.blockers():raise CandidateError('artifact_preview_waits_for_action_resolution')
   return self.artifact_preview_offer(args,g)
  if name=='check_last_action':return await self.status(g)
  if name=='repeat_pending_review':
   p=self.actions.pending
   if p and self.actions.clock()<=p['expires']:return dict(status='needs_confirmation',reply=p['prompt'],execution_started=False)
   if p:self.cancel_pending()
   return dict(status='no_pending_review',reply='There is no current unexpired review. Please make a fresh request.',execution_started=False)
  if name=='cancel_pending_action':
   mode=args.get('mode','discard');p=self.actions.pending;self.cancel_pending()
   result=dict(status='uncommitted_proposal_canceled',completed_actions_undone=False,mode=mode)
   if mode!='continue':
    contact=(p or {}).get('proposal',{}).get('intent',{}).get('recipient',{})
    recipient=contact.get('label') if isinstance(contact,dict) else contact if p and p['action']==GMAIL_ACTION and isinstance(contact,str) else None
    result['host_reply']='The unconfirmed proposal is canceled.' if mode=='discard' else 'The previous proposal is canceled. What would you like the new message to say'+(' to '+recipient if recipient else '')+'?'
   return result
  if name in self.registry.reads:
   self.progress('reading',name,g);value=await self.registry.read(name,g,args);self.check(g);self.progress('read_received',name,g);return value
  if name not in {d['name'] for d in self.action_declarations}:raise CandidateError('gateway_action_not_authorized')
  name,args=stream_call(name,args)
  Draft202012Validator(self.canonical_action_declarations[name]['parameters']).validate(args)
  key=fingerprint(name,args);self.ledger.load();prior=self.ledger.rows.get((self.goal,key))
  deferred=self.deferred_intents.get((self.goal,key))
  # Only a no-RPC prerequisite clarification can be prepared again, and only
  # after a new owner turn. A backend failure/uncertainty never enters this map.
  if prior and prior['state']=='NOT_DISPATCHED' and not prior.get('operation_id') and deferred and deferred['generation']<g:
   prior=None;self.deferred_intents.pop((self.goal,key),None)
  if prior and prior['state']=='WAITING_CONFIRMATION' and self.actions.pending and self.pending_key==(self.goal,key):
   # Same still-uncommitted proposal: re-present it, never prepare/dispatch it twice.
   p=self.actions.pending
   if self.actions.clock()<=p['expires']:return dict(status='needs_confirmation',reply=p['prompt'],execution_started=False)
   self.cancel_pending();return dict(status='no_pending_review',reply='That proposal expired. Please make a fresh request.',execution_started=False)
  if prior:
   record=self.journal.records.get(prior.get('operation_id'),{})
   if prior['state']=='VERIFIED' and record and await self.verify_record(record,g):
    receipt=await self.completion_receipt(name,args,g,record)
    self.check(g);self.progress('duplicate_suppressed',name,g);return dict(status='verified',verification='independent_readback',duplicate_suppressed=True,write_retried=False,reply='This action was already completed in this goal; its outcome was independently checked again. No action was repeated.',**receipt)
   if (prior['state'] in {'CANCELED','NOT_DISPATCHED'}
       and (prior.get('operation_id') is None and not record
            or record.get('operation_id')==prior.get('operation_id') and record.get('action')==name and record.get('state')==prior['state'])):
    # A retired proposal cannot be authorized or replayed. Return this known
    # non-executing current attempt to Onit without stopping ordinary speech.
    # This does not assert the outcome of an earlier action or create a review.
    self.check(g)
    return dict(status='retired_proposal_not_replayed',prior_state=prior['state'],execution_started=False,new_effect=False,write_retried=False,automatic_retry=False,pending_review_restored=False,requires_fresh_request=True,requires_fresh_review=True,
                reply='That earlier proposal is no longer active, so it cannot be confirmed now. I have not repeated it. Please make a fresh request if you still want the change, so I can show a new review.',
                planner_guidance='Explain this retired proposal without claiming its earlier outcome was saved or not saved. Do not restore its confirmation or recreate it during this turn. A fresh owner request and a fresh exact review are required for any later change.')
   raise CandidateError('gateway_existing_intent_not_replayed')
  blocked=self.blockers();cleanup=blocked and self.window_cleanup_allowed([dict(name=name,args=args)],g)
  if blocked and not cleanup:raise CandidateError('gateway_uncertain_outcome_requires_readback')
  if self.actions.pending:self.cancel_pending() # A validated replacement proposal revokes only the obsolete uncommitted review.
  self.ledger.record(self.goal,key,name,'REQUESTED');before=set(self.journal.records);self.progress('preparing',name,g)
  permit=None
  try:
   if cleanup:
    permit=_WindowCleanupPermit(self,[dict(name=name,args=args)],g,(self.goal,key))
    self._uncertain_cleanup_permit=permit;self.actions._uncertain_cleanup_permit=permit
   reply=await self.actions.propose(name,args,g);self.check(g)
  except BaseException:
   self.capture_failure(key,name,before);raise
  finally:
   if permit is not None:
    permit.closed=True
    if getattr(self,'_uncertain_cleanup_permit',None) is permit:self._uncertain_cleanup_permit=None
    if getattr(self.actions,'_uncertain_cleanup_permit',None) is permit:self.actions._uncertain_cleanup_permit=None
  p=self.actions.pending
  if p:
   self.ledger.record(self.goal,key,name,'WAITING_CONFIRMATION',p['operation_id']);self.pending_key=(self.goal,key);self.progress('waiting_confirmation',name,g)
   return dict(status='needs_confirmation',reply=p['prompt'],execution_started=False)
  completed=self.actions.last_completed;record=self.journal.records.get(completed['operation_id'],{}) if completed else {}
  if not completed or completed['action']!=name or completed['generation']!=g or record.get('state')!='VERIFIED':
   self.capture_failure(key,name,before)
   if name in {'propose_whatsapp_window','propose_contact_message'} and self.ledger.rows[(self.goal,key)]['state']=='NOT_DISPATCHED':
    # A refused window preparation has no external effect. Report it honestly
    # without killing unrelated conversation. No automatic effect retry.
    clarification=self.actions.preparation_clarification
    if clarification:
     self.deferred_intents[(self.goal,key)]=dict(generation=g,reason=clarification['reason'])
     pending_intent=dict(action=name,args=copy.deepcopy(args),source_goal=self.goal,source_generation=g,authority='uncommitted_model_draft_only; not send/call authorization')
     self.check(g);return dict(status='needs_clarification',execution_started=False,write_retried=False,reply=reply,reason=clarification['reason'],pending_intent=pending_intent)
    self.check(g);return dict(status='not_started',execution_started=False,write_retried=False,reply=reply)
   clarification=self.actions.preparation_clarification or {};proof=clarification.get('no_rpc_proof') or {}
   refused=self.ledger.rows[(self.goal,key)]
   if (refused['state']=='NOT_DISPATCHED' and refused.get('operation_id') is None
       and proof.get('action')==name and proof.get('generation')==g
       and re.fullmatch('[0-9a-f]{32}',proof.get('operation_id',''))
       and proof['operation_id'] not in self.journal.records
       and type(proof.get('rpc_calls_before')) is int and proof['rpc_calls_before']==proof.get('rpc_calls_after')==getattr(self.registry,'calls',None)
       and proof.get('journal_operations')==len(before)==len(self.journal.records) and before==set(self.journal.records)
       and proof.get('journal_unchanged') is True and proof.get('no_pending') is True and proof.get('no_completion') is True
       and self.actions.pending is None and self.actions.last_completed is None):
    # Return host facts to the native planner, not an irrelevant contact
    # question to the user. No deferred/retry entry or action authority exists.
    self.check(g);return dict(status='not_started',no_effect=True,context_lacking=[clarification['reason']],execution_started=False,write_retried=False,automatic_retry=False,planner_guidance='This proposal was rejected before any RPC or effect. Reconsider the latest user request and answer it or ask a relevant clarification. Do not repeat this proposal automatically.')
   raise CandidateError('gateway_action_failed_or_unverified')
  receipt=await self.completion_receipt(name,args,g,record)
  self.ledger.record(self.goal,key,name,'VERIFIED',completed['operation_id']);self.progress('verified',name,g)
  return dict(status='verified',verification='independent_readback',reply=reply,duplicate_suppressed=False,**receipt)
 async def completion_receipt(self,name,args,g,record):
  if name==GMAIL_ACTION:
   adapter=self.actions.adapters[name]
   if not await adapter.verify(record,g):raise CandidateError('gateway_gmail_sent_readback_unverified')
   return dict(verified_gmail=dict(recipient=record['verified_gmail_recipient'],status='sent_copy_observed',smtp_accepted=True,sent_copy_verified=True,delivery_confirmed=False,read_confirmed=False,automatic_retry=False))
  if name in MESSAGE_ACTIONS:
   adapter=self.actions.adapters[name]
   if not await adapter.verify(record,g):raise CandidateError('gateway_message_readback_unverified')
   status=record['verified_message_status']
   return dict(verified_message=dict(recipient=record.get('verified_recipient','self'),status=status,delivery_confirmed=status in {'delivered','read'},read_confirmed=status=='read'))
  # Play verification proves identity/decoder state. A requested startup volume
  # also needs fresh numeric readback before the model may claim that change.
  # This read is part of guarded verification, never another model plan/effect.
  if name=='propose_stream_control':
   try:
    from me3_stream.service import snapshot,verify,digest
    data=await self.registry.rpc('soundcloud','read_stream_operation',g,dict(operation_id=record['operation_id']));self.check(g)
    row=data.get('row')
    if not row or row.get('state')!='COMPLETED' or row.get('expected_digest')!=record['expected_digest'] or digest(row['intent'])!=record['expected_digest']:raise CandidateError('gateway_stream_readback_mismatch')
    s=snapshot(data['snapshot'],row['owner'])
    baseline={**s,'sequence':row['ack_sequence'],'track_url':row['intent'].get('track_url',row['track_before'])}
    verify(row['intent'],baseline,s,record['operation_id'])
    return dict(verified_music=dict(source='SoundCloud',command=args['command'],state='stopped' if args['command']=='stop' else 'paused' if s['paused'] else 'playing',volume_percent=s['volume'],track_url=s['track_url']))
   except BaseException:
    self.journal.record(record['operation_id'],name,'UNCERTAIN',record['expected_digest'])
    self.ledger.record(self.goal,fingerprint(name,args),name,'UNCERTAIN',record['operation_id'])
    raise
  if name!='propose_music_control' or args.get('command')!='play':return self.music_receipt(name,args)
  try:
   value=await self.registry.read('music__get_playback_status',g,{});self.check(g)
   if not (value.get('state') in {'playing','ended'} and value.get('track_id')==args['track_id'] and type(value.get('volume_percent')) is int and value['volume_percent']==args.get('volume',33) and type(value.get('consumed_frames')) is int and value['consumed_frames']>0 and not value.get('error')):raise CandidateError('gateway_play_volume_readback_mismatch')
   return dict(verified_music=dict(command='play',state=value['state'],volume_percent=value['volume_percent'],track_id=value['track_id']))
  except BaseException:
   self.journal.record(record['operation_id'],name,'UNCERTAIN',record['expected_digest'])
   self.ledger.record(self.goal,fingerprint(name,args),name,'UNCERTAIN',record['operation_id'])
   raise
 @staticmethod
 def music_receipt(name,args):
  if name=='propose_music_control' and args.get('command')=='volume' and type(args.get('volume')) is int:
   return dict(verified_music=dict(command=args['command'],volume_percent=args['volume']))
  if name=='propose_music_control' and args.get('command') in {'stop','pause'}:
   return dict(verified_music=dict(command=args['command'],state={'stop':'stopped','pause':'paused'}[args['command']]))
  return {}
 def capture_failure(self,key,name,before):
  rows=[v for op,v in self.journal.records.items() if op not in before and v['action']==name];record=rows[-1] if rows else {}
  state=record.get('state');state=state if state in {'VERIFIED','UNCERTAIN','CANCELED','NOT_DISPATCHED'} else 'UNCERTAIN' if state=='DISPATCHED' else 'NOT_DISPATCHED'
  self.ledger.record(self.goal,key,name,state,record.get('operation_id'));self.progress(state.lower(),name,self.generation)
 async def confirm(self,history,g,*,_host_acceptance=None):
  # Host-only API. No confirmation tool/schema is ever sent to Onit.
  self.check(g)
  raw=latest_text(history) if history and history[-1].get('type')=='user_input' else ''
  if _host_acceptance is not None:
   from onit_candidate.pending_confirmation import admit_gateway
   if not admit_gateway(_host_acceptance,self,history,g):return _host_acceptance.expire()
  elif confirmation_intent(raw)!='affirmative':raise CandidateError('gateway_direct_confirmation_required')
  p=self.actions.pending
  if not p or not self.pending_key:raise CandidateError('gateway_no_pending_confirmation')
  from onit_candidate.pending_confirmation import ConfirmationExpiryFence
  expiry_fence=ConfirmationExpiryFence(self,history,g)
  if expiry_fence.expired_before_actions():
   self.cancel_pending()
   return dict(status='no_pending_review',execution_started=False,write_retried=False,reply='That proposal expired. Please make a fresh request for a new review.')
  row=self.ledger.rows[self.pending_key]
  if p['action']=='propose_whatsapp_call':p['_handoff_binding']=dict(goal_id=row['goal_id'],intent_key=row['intent_key'],ledger_path=str(self.ledger.path),journal_path=str(self.journal.path),root=str(self.ledger.root))
  try:reply=await self.actions.confirm(history,g)
  except BaseException:
   record=self.journal.records.get(p['operation_id'],{});state=record.get('state');self.ledger.record(row['goal_id'],row['intent_key'],row['action'],state if state in STATES else 'UNCERTAIN',p['operation_id']);self.pending_key=None;raise
  record=self.journal.records.get(p['operation_id'],{})
  if self.actions.pending is not None:return dict(status='needs_confirmation',reply=p['prompt'],execution_started=False)
  self.pending_key=None;state=record.get('state');self.ledger.record(row['goal_id'],row['intent_key'],row['action'],state if state in STATES else 'UNCERTAIN',p['operation_id'])
  if state=='READ_UNVERIFIED' and row['action']==GMAIL_READ_ACTION:
   self.check(g);return dict(status='unverified',read_only=True,read_result_verified=False,automatic_retry=False,reply=reply)
  if state=='HANDOFF_AUTHORIZED' and row['action']=='propose_whatsapp_call':
   return dict(status='call_handoff',verification='not_established',verified=False,execution_started=False,operation_id=p['operation_id'],reply=reply)
  if state!='VERIFIED':
   if state=='CANCELED' and expiry_fence.retired_without_dispatch(p,row):
    self.check(g)
    return dict(status='no_pending_review',execution_started=False,write_retried=False,reply='That proposal expired. Please make a fresh request for a new review.')
   # A scoped message outcome may be unknown after a real send. Report that
   # fact without killing unrelated conversation. Durable blockers still
   # prohibit effects/replay; cancellation and broken invariants stay fatal.
   if row['action'] in MESSAGE_ACTIONS|{GMAIL_ACTION} and state in {'UNCERTAIN','CANCELED','NOT_DISPATCHED'}:
    self.check(g)
    if row['action']==GMAIL_ACTION:
     status=p.get('gmail_operation_status','unknown')
     if status=='SMTP_ACCEPTED':reply='Gmail accepted the message for submission, but I could not verify its exact copy in Sent. Delivery and reading are not confirmed. I will not send it again automatically; you can ask me to check its status.'
     return dict(status='unverified',verification='not_established',write_retried=False,execution_started=state=='UNCERTAIN',gmail_operation_status=status,sent_copy_verified=False,delivery_confirmed=False,read_confirmed=False,reply=reply)
    return dict(status='unverified',verification='not_established',write_retried=False,execution_started=state=='UNCERTAIN',reply=reply)
   raise CandidateError('gateway_confirmation_not_verified')
  if row['action']=='propose_productivity':
   references=getattr(self.registry,'productivity_records',None)
   if references is not None:references.observe_verified(p,record)
  if row['action']==GMAIL_READ_ACTION:
   from me3_assistant.registry import public
   result=public(copy.deepcopy(p['verified_gmail_read_result']))
   self.registry.gmail_read_context=dict(owner=self.registry.communication_owner,observed_monotonic=time.monotonic(),result=result,scope=copy.deepcopy(p['verified_gmail_read_intent']),content_is_untrusted=True,action_authority=False)
   self.check(g);return dict(status='verified',verification='confirmed_read_and_private_cached_result',read_only=True,reply=reply,verified_gmail_read=dict(result=result,scope=copy.deepcopy(p['verified_gmail_read_intent']),content_is_untrusted=True,action_authority=False))
  try:receipt=await self.completion_receipt(row['action'],{},g,record) if row['action'] in MESSAGE_ACTIONS|{GMAIL_ACTION} else {}
  except BaseException as exc:
   self.journal.record(p['operation_id'],row['action'],'UNCERTAIN',record['expected_digest']);self.ledger.record(row['goal_id'],row['intent_key'],row['action'],'UNCERTAIN',p['operation_id'])
   if isinstance(exc,Exception) and row['action'] in MESSAGE_ACTIONS|{GMAIL_ACTION}:
    self.check(g);return dict(status='unverified',verification='not_established',write_retried=False,execution_started=True,reply='I could not verify the Gmail message outcome. I will not send it again automatically. You can check Gmail or ask me to check its status; you can still talk with me.' if row['action']==GMAIL_ACTION else 'I could not verify the message outcome. I will not send it again automatically. Please check WhatsApp; you can still talk with me.')
   raise
  self.check(g);return dict(status='verified',verification='independent_readback',reply=reply,**receipt)
