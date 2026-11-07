"""Explicit, source-bound benchmark attenuation; never a standing permission.

The parent freezes the contract hash after reviewing source, queue and counters.
This module makes no requests. Existing normal authorization/accounting remains
mandatory; the finite wrapper can only reduce that permission.
"""
import asyncio,copy,hashlib,json,time
from pathlib import Path
from normal_session_path import allocate,allocate_intent_ledger

ROLES={'asr','reasoning','tts','commit_item'}
PER_SESSION={'asr':16,'reasoning':16,'tts':64,'commit_item':0}

def need(value,code):
 if not value:raise ValueError(code)

def capacities(rounds,phrases=10,runs=3):
 need(type(rounds) is int and 0<=rounds<=30,'rounds_0_to_30_required')
 need(type(phrases) is int and 0<=phrases<=10 and type(runs) is int and 0<=runs<=3,'bounded_stage_plan_required')
 need((phrases==0)==(runs==0),'zero_stage_plan_must_be_explicit')
 stages=phrases*runs;sessions=14*rounds+stages
 need(sessions>0,'nonempty_benchmark_plan_required')
 return dict(rounds=rounds,phrases=phrases,runs=runs,stage_executions=stages,queue_items=10*rounds+stages,executions=sessions,
  reservations={k:v*sessions for k,v in PER_SESSION.items()},local_scoring_transcriptions=22*rounds+stages,
  judge_requests=5*rounds,judge_transport_attempts=5*rounds,judge_retry_count=0)

def validate(document,source,config,now):
 need(document.get('version')==1 and document.get('execution_approved') is True,'reviewed_benchmark_execution_contract_required')
 need(document.get('source_revision')==source['revision'] and document.get('config_sha256')==config['sha256'],'benchmark_source_config_changed')
 need(document.get('course_commit')=='57eaddbc188ce9a19b8fd002b575f703426956f4','pinned_course_required')
 expected=capacities(document.get('rounds'),document.get('phrases'),document.get('runs'))
 if document.get('partial_selected_plan') is not None:
  need(document['partial_selected_plan']==dict(cohort='007',queue_items=3,executions=5,reply_expected_executions=4,prior006_unstarted_only=True) and (document.get('rounds'),document.get('phrases'),document.get('runs'))==(1,0,0),'only_exact007_partial_selection_required')
  need(document.get('behavior_plan_sha256')=='729b3fbe45084970cb53a020c14317219f746652eca47aa458754a573a2d5a3d','exact007_selected_plan_required')
  expected=dict(expected,queue_items=3,executions=5,reservations=dict(asr=80,reasoning=80,tts=320,commit_item=0),local_scoring_transcriptions=9)
 need(document.get('capacities')==expected,'exact_derived_capacities_required')
 need(document.get('per_session')==PER_SESSION and document.get('max_seconds')==180 and document.get('max_turns')==8,'bounded_benchmark_session_required')
 need(document.get('fresh_context_per_execution') is True and document.get('automatic_retry') is False and document.get('effects')==[],'fresh_readonly_no_retry_benchmark_required')
 need(type(document.get('expires_unix')) in {int,float} and now<document['expires_unix']<=now+86400,'fresh_finite_benchmark_expiry_required')
 start=document.get('budget_start',{})
 need(set(start)==ROLES and all(type(n) is int and n>=0 for n in start.values()),'exact_counter_baseline_required')
 return document

class FiniteBudget:
 def __init__(self,underlying,document,usage,session_start):
  self.underlying=underlying;self.document=document;self.usage=usage;self.session_start=dict(session_start)
  self.exhausted=underlying.exhausted
 def remaining(self,role):
  need(role in ROLES,'invalid_benchmark_role')
  underlying_remaining=self.underlying.remaining(role) # Settle conservatively charged pending reservation first.
  current=self.usage()
  need(set(current)==ROLES and all(type(current[k]) is int and current[k]>=self.session_start[k]>=self.document['budget_start'][k] for k in ROLES),'benchmark_counters_decreased')
  return max(0,min(underlying_remaining,PER_SESSION[role]-(current[role]-self.session_start[role]),
   self.document['capacities']['reservations'][role]-(current[role]-self.document['budget_start'][role])))
 def permitted(self,role):return self.remaining(role)>0
 def reserve(self,role):
  if time.time()>=self.document['expires_unix'] or self.remaining(role)<=0:
   self.exhausted.set();raise ValueError('benchmark_attempt_allowance_exhausted_no_retry')
  return self.underlying.reserve(role) # Original cumulative counter, rate and no-refund guard.

class BenchmarkSession:
 def __init__(self,course,log_dir,contract_path,contract_sha256):
  path=Path(contract_path);raw=path.read_bytes()
  need(isinstance(contract_sha256,str) and len(contract_sha256)==64 and hashlib.sha256(raw).hexdigest()==contract_sha256,'frozen_benchmark_contract_required')
  self.path=path;self.sha256=contract_sha256;self.document=json.loads(raw);self.course=course
  self.log_parent=Path(log_dir).absolute();self.log_dir=self.log_parent/'unstarted';self.bridge=None;self.processor=None;self.fresh=True
  self.journal_path=self.log_dir/'actions.jsonl';self.action_directory=self.log_dir/'action-budgets'
 def authorize(self,root,source,config,*,initial_history,context,callbacks,warm_floor,main_prewarm,validated_delivery,task_recovery):
  need(self.fresh and self.path.read_bytes() and hashlib.sha256(self.path.read_bytes()).hexdigest()==self.sha256,'one_frozen_benchmark_session_required')
  need(not initial_history and not context.history and getattr(context,'pending',None) is None,'fresh_benchmark_context_required')
  need(all(v is None for v in callbacks) and warm_floor is True and validated_delivery is True and main_prewarm is False and task_recovery is False,'unchanged_qualified_voice_lanes_required')
  validate(self.document,source,config,time.time());self.fresh=False
  self.root=Path(root);self.usage_path=self.root/'evidence/completion/autonomous-20261020-001/budget.json'
  from benchmark_m6.storage import read,atomic
  from me3_assistant.guard import lock
  state_path=self.path.with_suffix('.sessions.json')
  with lock(self.path.with_suffix('.lock')):
   state=read(state_path) if state_path.exists() else {'contract_sha256':self.sha256,'started':0}
   need(state=={'contract_sha256':self.sha256,'started':state.get('started')} and type(state['started']) is int and 0<=state['started']<self.document['capacities']['executions'],'benchmark_session_cap_or_contract_changed')
   current=read(self.usage_path);need(set(current)==ROLES and all(type(current[k]) is int and current[k]>=self.document['budget_start'][k] for k in ROLES),'benchmark_baseline_counter_conflict')
   self.session_start=dict(current);ordinal=state['started']+1
   atomic(state_path,{**state,'started':ordinal})
  self.log_dir,self.session_pointer,self.session_pointer_sha256=allocate(root,self.log_parent,self.sha256,ordinal,self.document['source_revision'])
  self.journal_path=self.log_dir/'actions.jsonl';self.action_directory=self.log_dir/'action-budgets'
  self.intent_ledger_path=allocate_intent_ledger(root,self.sha256,ordinal)
 def budget(self,underlying):
  from benchmark_m6.storage import read
  return FiniteBudget(underlying,self.document,lambda:read(self.usage_path),self.session_start)
 def attenuate(self,config,grant):
  config=copy.deepcopy(config)
  utility=[s for s in config['servers'] if s['id']=='utilities' and s['module']=='me3_assistant.utility_server' and s['args']==[] and s['identity']=='me3' and s['role']=='utility' and set(s['read_tools'])=={'calculate_expression','get_current_time'}]
  need(len(utility)==1,'source_bound_public_utility_MCP_required')
  config.update(servers=utility,actions=[])
  config['runtime'].update(max_session_seconds=180,max_turns=8)
  # Deliberately omit standing/private/music/account rights from model/Registry.
  grant={k:grant[k] for k in ('backend','audio_mode') if k in grant}
  grant.update(allowed_actions=[],workspace_access=False,local_action_limits={})
  return config,grant
 def transport_factory(self,settings,events):
  from digital_transport import DigitalTransport
  need(self.bridge is None,'one_digital_transport_required')
  self.bridge=DigitalTransport(self.course,settings,events,ready_metadata=dict(source_revision=self.document['source_revision'],contract_sha256=self.sha256))
  from measurement_host import attach
  attach(self)
  return self.bridge.factory
 def observe(self,result,settings,events):
  from digital_observe import attach
  need(self.bridge is not None and result[2] is self.bridge,'digital_pipeline_identity_required')
  self.processor=result[3];attach(self.processor,self.bridge,events)
  from stage_observer import attach as attach_stages
  attach_stages(self,events)
  self.bridge.stage_observer=self.stage_observer
 def startup(self):self.bridge.bind_voice_events()
