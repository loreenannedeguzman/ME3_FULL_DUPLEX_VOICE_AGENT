"""Benchmark-only packet lineage; no model/permission/media decisions."""
import hashlib,json,os
from pathlib import Path
from measurement_lineage import Trace,attach_core

def attach(resource):
 if getattr(resource,'measurement_trace',None) is not None:raise ValueError('single_measurement_attachment_required')
 trace=Trace('assistant_process:monotonic_ns',maximum_rows=100000)
 attach_core(resource.bridge.core,trace,session_id=resource.bridge.observation_session)
 resource.measurement_trace=trace

def flush(resource):
 trace=getattr(resource,'measurement_trace',None)
 if trace is None or getattr(resource,'measurement_saved',False):return
 path=Path(resource.log_dir)/'packet-lineage.json'
 value=dict(version=1,source_revision=resource.document['source_revision'],contract_sha256=resource.sha256,
  physical_audio=False,bridge_cleanup_complete=resource.bridge.cleanup_complete,
  observer_sha256=hashlib.sha256(Path(__file__).with_name('measurement_lineage.py').read_bytes()).hexdigest(),trace=trace.snapshot())
 raw=(json.dumps(value,separators=(',',':'))+'\n').encode()
 with path.open('xb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
 resource.measurement_path=path;resource.measurement_saved=True
