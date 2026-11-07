"""Load only reviewed adapter seams in an isolated benchmark process.

Production files and native index stay untouched. The existing identity checks
still validate the full installed candidate, independently of adapter hashes.
"""
import hashlib,importlib,importlib.util,json,sys
from pathlib import Path
P=Path(__file__).resolve().parent
INSTALLED=False
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def install(root,document):
 global INSTALLED
 root=Path(root).resolve();bindings=json.loads((P/'source-bindings.json').read_bytes())
 if document.get('source_revision')!=bindings['source_revision']:raise ValueError('adapter_candidate_revision_mismatch')
 for name,row in bindings['files'].items():
  if sha(root/name)!=row['sha256']:raise ValueError('installed_adapter_source_binding_changed:'+name)
 for name in ['overlay/onit_candidate/conversation.py','overlay/onit_candidate/voice.py','overlay/me3_completion/pipeline.py','benchmark_resources.py','normal_session_path.py','digital_core.py','digital_transport.py','digital_observe.py','owned_shutdown.py','normal_entry.py','runtime_bootstrap.py','source-bindings.json','measurement_host.py','measurement_lineage.py','measurement_rig.py','execution_gate.py','stage_observer.py']:
  if document.get('adapter_hashes',{}).get(name)!=sha(P/name):raise ValueError('frozen_adapter_hash_required:'+name)
 if INSTALLED:return
 sys.path.insert(0,str(P))
 for name in ['onit_candidate.conversation','me3_completion.pipeline','onit_candidate.voice']:
  path=P/'overlay'/(name.replace('.','/')+'.py');parent,_,leaf=name.rpartition('.')
  importlib.import_module(parent)
  spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec)
  sys.modules[name]=module;spec.loader.exec_module(module);setattr(sys.modules[parent],leaf,module)
 INSTALLED=True
