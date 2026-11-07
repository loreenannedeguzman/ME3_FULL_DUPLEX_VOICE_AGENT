"""Finite external launcher: exact course run_once, normal entry, 44 fresh sessions.

Never installed as normal launch command. No retries or default-route changes.
"""
import argparse,asyncio,hashlib,importlib.util,json,os,sys,time,traceback
from pathlib import Path
P=Path(__file__).resolve().parent
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def need(v,c):
 if not v:raise ValueError(c)
def save(p,v):
 with p.open('x',encoding='utf-8') as f:json.dump(v,f,indent=2);f.write('\n')
def main():
 parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,required=True);parser.add_argument('--course',type=Path,required=True);parser.add_argument('--out',type=Path,required=True);parser.add_argument('--contract-sha256',required=True);a=parser.parse_args()
 need(sys.platform=='linux','actual_Pi_runtime_required');root=a.root.resolve();course=a.course.resolve();out=a.out.resolve()
 need(out.parent==P and not out.exists(),'fresh_owned_collection_output_required');out.mkdir(mode=0o700)
 contract=P/'resource-contract.FINAL.json';need(sha(contract)==a.contract_sha256,'exact_frozen_resource_contract_required');doc=json.loads(contract.read_bytes())
 need(doc['capacities']['executions']==44 and doc['rounds']==1 and doc['phrases']==10 and doc['runs']==3 and doc['seed']==0,'reviewed_single_round_stage_plan_required')
 need(json.loads((P/'resource-binding.FINAL.json').read_bytes())==dict(reviewed_execution_approved=True,resource_contract_sha256=a.contract_sha256),'reviewed_contract_binding_required')
 for n,h in doc['adapter_hashes'].items():need(sha(P/n)==h,'adapter_changed:'+n)
 manifest=json.loads((P/'manifest.DRAFT.json').read_bytes())
 for n,h in manifest['course_hashes'].items():need(sha(course/n)==h,'course_changed:'+n)
 sys.path[:0]=[str(P),str(course),str(course/'adapters'),str(root)];os.chdir(root)
 import normal_entry
 spec=importlib.util.spec_from_file_location('course_launch441',course/'launch_assistant.py');launch=importlib.util.module_from_spec(spec);spec.loader.exec_module(launch)
 class Entry:
  audio_rates=staticmethod(normal_entry.audio_rates)
  describe=staticmethod(normal_entry.describe)
  session=None
  async def build(self,transport,log_dir):
   run,cancel=await normal_entry.build(transport,log_dir)
   need(isinstance(cancel.__self__,normal_entry.Session),'actual_normal_session_owner_required')
   self.session=cancel.__self__;return run,cancel
 entry=Entry();rows=[];failure=None
 try:
  for ordinal in range(1,45):
   need(time.time()<doc['expires_unix'],'finite_collection_expired_no_retry')
   # --no-marks prevents duplicate standard-frame observations. Explicit ME3
   # stage_observer sends marks on this SAME websocket; upstream stats retained.
   asyncio.run(launch.run_once('ws://127.0.0.1:18894/?out_sr=48000',entry,P/'normal-sessions',False,
    stats_hz=1.,stats_procs='asr_mcp=onit_candidate.asr_server asr,tts_mcp=onit_candidate.asr_server tts,tools_mcp=me3_assistant.utility_server',marks=False))
   r=entry.session.resource;b=r.bridge
   need(b.peer_end_consumed and b.peer_end.is_set() and b.cleanup_complete and not b.failure.is_set(),'successful_exact_peer_reset_and_cleanup_required')
   stage=json.loads((r.log_dir/'stage-observation.json').read_bytes());need(stage['complete'] and stage['pending']==0,'actual_stage_delivery_required')
   row=dict(execution_ordinal=ordinal,session=b.observation_session,source_revision=doc['source_revision'],normal_log_dir=str(r.log_dir.relative_to(root)),
    stage_receipt_sha256=sha(r.log_dir/'stage-observation.json'),packet_lineage_sha256=sha(r.log_dir/'packet-lineage.json'),cleanup_verified=True)
   rows.append(row);save(out/('%03d.json'%ordinal),row)
 except BaseException as e:failure=type(e).__name__; (out/'exception.txt').write_text(traceback.format_exc(),encoding='utf-8')
 value=dict(status='BENCHMARK441_PI_44_SESSIONS_COLLECTED' if failure is None and len(rows)==44 else 'STOPPED',failure=failure,completed_executions=len(rows),maximum_executions=44,sessions=rows,contract_sha256=a.contract_sha256,source_revision=doc['source_revision'],automatic_retry=False,physical_audio=False,stats='Exact upstream run_once/stats_loop at1Hz; acceptance requires actual received samples',stage_clock='Pi monotonic; course arrival offset only for trial association')
 save(out/'receipt.json',value);print(json.dumps(value));return failure is not None
if __name__=='__main__':raise SystemExit(main())
