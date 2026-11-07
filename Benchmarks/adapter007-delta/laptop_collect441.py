"""Exact updated course CLI with finite reset/observer adaptations, no scoring.

Root starts the owned SSH route and Pi launcher separately in one orchestration.
"""
import argparse,hashlib,json,sys,traceback
from pathlib import Path
P=Path(__file__).resolve().parent
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def need(v,c):
 if not v:raise ValueError(c)
def main():
 p=argparse.ArgumentParser();p.add_argument('--course',type=Path,required=True);p.add_argument('--datasets',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--contract-sha256',required=True);a=p.parse_args()
 contract=P/'resource-contract.FINAL.json';need(sha(contract)==a.contract_sha256,'exact_contract_required');d=json.loads(contract.read_bytes());m=json.loads((P/'manifest.DRAFT.json').read_bytes())
 need(d['student']=='000000000' and d['seed']==0 and d['capacities']['executions']==5,'exact_plan_required')
 for n,h in m['course_hashes'].items():need(sha(a.course/n)==h,'course_changed:'+n)
 need(not a.out.exists(),'single_use_collection_output_required');a.out.mkdir(parents=True)
 sys.path[:0]=[str(P),str(a.course),str(a.course/'adapters')]
 import fdbench.cli as cli,fdbench.rig.direct as direct,fdbench.runner as runner
 from measurement_rig import guarded_rig,stop_on_first_error
 measured=guarded_rig(direct.DirectRig,source_revision=d['source_revision'],contract_sha256=a.contract_sha256,maximum_executions=5)
 class LoopbackRig(measured):
  def __init__(self,*args,**kwargs):
   need(not args and kwargs.get('port')==18893,'exact_reviewed_loopback_port_required')
   kwargs['host']='127.0.0.1';kwargs['measurement_dir']=a.out/'lineage';super().__init__(**kwargs)
 direct.DirectRig=LoopbackRig;stop_on_first_error(runner.Runner)
 # Preserve exact previously predeclared behavior clip selection; no stage
 # trials, hidden retry, or results-dependent replacement clips.
 from fdbench.queue import Plan
 need(sha(P/'behavior-plan.PREDECLARED.json')==d['behavior_plan_sha256'],'exact_predeclared_behavior_plan_required')
 selected=json.loads((P/'behavior-plan.PREDECLARED.json').read_bytes())['plan']
 def selected_plan(ds,rounds,seed,phrases=0,runs=0):
  need((rounds,seed,phrases,runs)==(1,0,0,0),'behavior_only_no_stages_required')
  for item in selected['items']:need(ds.get(item['group'],item['clip_id']).id==item['clip_id'],'public_selected_clip_required')
  return Plan.from_json(selected)
 runner.build_plan=selected_plan
 cli.RUNS_DIR=a.out/'runs'
 args=cli.build_parser().parse_args(['run','--student','000000000','--rounds','1','--phrases','0','--runs','0','--seed','0','--connect','direct','--where','remote','--port','18893','--marks-port','0','--datasets',str(a.datasets),'--judge-provider','none','--no-score','--no-live','--yes','--recordings','r'])
 failure=None;rc=1
 try:rc=cli.cmd_run(args)
 except BaseException as e:failure=type(e).__name__; (a.out/'exception.txt').write_text(traceback.format_exc(),encoding='utf-8')
 queues=list(a.out.glob('runs/000000000/*/queue.json'));need(len(queues)<=1,'one_fresh_run_required')
 queue=json.loads(queues[0].read_bytes()) if queues else None
 complete=bool(queue and len(queue['items'])==3 and all(i['status']=='done' for i in queue['items']))
 value=dict(status='BENCHMARK441_LAPTOP_COLLECTION_COMPLETE_UNSCORED' if failure is None and rc==0 and complete else 'STOPPED',failure=failure,contract_sha256=a.contract_sha256,course_commit=m['course_commit'],source_revision=d['source_revision'],queue_items=3,actual_execution_ceiling=5,queue_path=str(queues[0]) if queues else None,queue_complete=complete,scoring=False,judge_requests=0,automatic_retry=False,route='REVERSE_SSH_LOOPBACK_397; transport overhead included',timing_marks='Pi stage differences; arrival offset only associates trial; no crosshost clock precision claim')
 with (a.out/'receipt.json').open('x',encoding='utf-8') as f:json.dump(value,f,indent=2);f.write('\n')
 print(json.dumps(value));return value['status']=='STOPPED'
if __name__=='__main__':raise SystemExit(main())
