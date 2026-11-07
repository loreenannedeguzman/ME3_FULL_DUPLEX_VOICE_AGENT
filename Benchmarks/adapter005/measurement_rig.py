"""Explicit observed benchmark Rig, retaining original guarded protocol."""
import hashlib,json,os
from pathlib import Path
from execution_gate import guarded_rig as original_guarded,stop_on_first_error as original_stop
from measurement_lineage import Trace,observed_rig

def guarded_rig(base,*,source_revision,contract_sha256,maximum_executions,timeout=90):
 # Lazy rows, finite derived cap. No PCM/text retention. Exhaustion invalidates
 # measurement; it cannot authorize, modify or restart an execution.
 trace=Trace('Rig_process:monotonic_ns',maximum_rows=100000*maximum_executions)
 observed=observed_rig(original_guarded(base,source_revision=source_revision,contract_sha256=contract_sha256,maximum_executions=maximum_executions,timeout=timeout),trace)
 class MeasuredRig(observed):
  def __init__(self,*args,measurement_dir=None,**kwargs):
   if getattr(trace,'constructed',False):raise ValueError('one_owned_measured_rig_instance_required')
   trace.constructed=True;super().__init__(*args,**kwargs);self.measurement_trace=trace;self.measurement_dir=Path(measurement_dir) if measurement_dir is not None else None;self.measurement_saved=False
  def save_measurement(self):
   if self.measurement_saved:return
   if self.measurement_dir is None:raise ValueError('owned_measurement_directory_required')
   value=dict(version=1,source_revision=source_revision,contract_sha256=contract_sha256,physical_audio=False,
    sessions=list(self.me3_sessions),guard_failed=self._me3_failed,
    observer_sha256=hashlib.sha256(Path(__file__).with_name('measurement_lineage.py').read_bytes()).hexdigest(),trace=trace.snapshot())
   self.measurement_dir.mkdir(parents=True,exist_ok=True)
   path=self.measurement_dir/'rig-packet-lineage.json'
   with path.open('x',encoding='utf-8') as stream:json.dump(value,stream,separators=(',',':'));stream.write('\n');stream.flush();os.fsync(stream.fileno())
   self.measurement_saved=True;self.measurement_path=path
  def close(self):
   try:return super().close()
   finally:
    if self.measurement_dir is not None:self.save_measurement()
 return MeasuredRig

def stop_on_first_error(runner_class):
 original_stop(runner_class);original=runner_class._run_trial
 def run(self,plan,row,table):
  self.rig.bind_trial(plan.trial_id)
  return original(self,plan,row,table)
 runner_class._run_trial=run
 return runner_class
