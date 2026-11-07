"""Parent-only outer wrapper: exact current source/GUI/usage around one test child."""
import argparse,hashlib,http.cookiejar,json,os,subprocess,sys,traceback,urllib.request
from pathlib import Path
P=Path(__file__).resolve().parent
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def need(v,c):
 if not v:raise ValueError(c)
def save(p,v):
 with p.open('x',encoding='utf-8') as f:json.dump(v,f,indent=2);f.write('\n')
def snapshot(root,spec):
 source=json.loads((root/'evidence/assistant/candidate-source.json').read_bytes())
 need(source['revision']==spec['source_revision'] and source['source_hashes']==spec['source_hashes'],'exact_current_source_required')
 for n,h in spec['source_hashes'].items():need(sha(root/n)==h,'source_changed:'+n)
 index=json.loads((root/'onit_candidate/candidate-files.json').read_bytes())
 need(index==spec['native_index'] and sha(root/'onit_candidate/candidate-files.json')==spec['native_index_sha256'],'current_native_index_required')
 for n,h in index.items():need(sha(root/'onit_candidate'/n)==h,'native_changed:'+n)
 protected=['evidence/assistant/operating-authorization.json','evidence/assistant/operation-state.json','evidence/completion/autonomous-20261020-001/budget.json','evidence/assistant/authorization.json','evidence/assistant/text-authorization.json','data/assistant-actions.jsonl','data/onit-action-intents.jsonl','data/whatsapp-contact-message-operations.sqlite3','data/agent-workspace/.manifest.json','evidence/assistant/candidate-source.json','onit_candidate/candidate-files.json','config/assistant.json','config/operating_policy.json']
 hashes={n:sha(root/n) if (root/n).is_file() else None for n in protected}
 usage=json.loads((root/protected[2]).read_bytes());need(usage==spec['expected_usage'],'exact_reviewed_usage_required')
 need(json.loads((root/protected[1]).read_bytes())['pending'] is None,'no_pending_reservation_required')
 opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
 with opener.open('http://127.0.0.1:8765/',timeout=4) as r:r.read(1048576)
 with opener.open('http://127.0.0.1:8765/api/state',timeout=4) as r:gui=json.load(r)
 identity={k:gui[k] for k in spec['expected_GUI_identity']}
 need(identity==spec['expected_GUI_identity'] and gui['active'] is False,'exact_inactive_owner_GUI_required')
 return dict(protected=hashes,usage=usage,GUI_identity_sha256=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest(),source_revision=source['revision'])
def main():
 a=argparse.ArgumentParser();a.add_argument('--root',type=Path,required=True);a.add_argument('--course',type=Path,required=True);a.add_argument('--out',type=Path,required=True);a.add_argument('--inputs-sha256',required=True);v=a.parse_args()
 specpath=P/'qualification-inputs.FINAL.json';need(sha(specpath)==v.inputs_sha256,'exact_reviewed_inputs_required');spec=json.loads(specpath.read_bytes());need(spec['parent_reviewed'] is True,'parent_review_required')
 for n,h in spec['adapter_files'].items():need(sha(P/n)==h,'adapter_changed:'+n)
 root=v.root.resolve();out=v.out.resolve();need(out.parent==P and not out.exists(),'owned_single_use_output_required');out.mkdir(mode=0o700)
 before=snapshot(root,spec);save(out/'before.json',before);failure=None;child=None;after=None
 try:
  q=subprocess.run([sys.executable,'-P','-B','-X','utf8',str(P/'qualify_suspend441.py'),'--root',str(root),'--course',str(v.course),'--out',str(out/'checks'),'--actual-transport','--accepted-only'],capture_output=True,timeout=90)
  (out/'stdout.txt').write_bytes(q.stdout);(out/'stderr.txt').write_bytes(q.stderr)
  need(q.returncode==0,'focused_child_failed');child=json.loads((out/'checks/receipt.json').read_bytes())
  need(child['status']=='BENCHMARK441_CHANGED_PATH_SOFTWARE_PASS' and child['tests']==1 and child['failures']==child['errors']==child['skipped']==0,'one_actual_suspend_buffer_check_required')
 except BaseException as e:failure=type(e).__name__; (out/'exception.txt').write_text(traceback.format_exc(),encoding='utf-8')
 finally:
  after=snapshot(root,spec);save(out/'after.json',after)
 prior=json.loads((P/'prior002-receipt.json').read_bytes());prior003=json.loads((P/'prior003-receipt.json').read_bytes());prior004=json.loads((P/'prior004-receipt.json').read_bytes())
 checks=dict(source_native_private_usage_GUI_preserved=before==after,actual_current_suspend_buffer_write_lifecycle=bool(child and child['actual_current_transport'] and child['tests']==1),prior_7_plus_1_plus_1_current_Pi_checks_reused=prior004['tests']==1 and all(prior004['checks'].values()) and prior003['tests']==1 and all(prior003['checks'].values()) and prior['status']=='BENCHMARK441_CURRENT_PI_CHANGED_PATH_PASS' and prior['tests']==7 and all(prior['checks'].values()),no_provider_or_device=True)
 value=dict(status='BENCHMARK441_CURRENT_PI_CHANGED_PATH_PASS' if failure is None and all(checks.values()) else 'STOPPED',failure=failure,checks=checks,source_revision=spec['source_revision'],inputs_sha256=v.inputs_sha256,child_receipt_sha256=sha(out/'checks/receipt.json') if child else None,tests=child['tests'] if child else None,provider_requests=0,benchmark_trials=0,scoring=False,source_changed=False)
 save(out/'receipt.json',value);print(json.dumps(value));return value['status']=='STOPPED'
if __name__=='__main__':raise SystemExit(main())
