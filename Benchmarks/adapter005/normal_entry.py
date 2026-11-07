"""Course entry: actual normal voice lifecycle, explicit frozen resource grant.

No environment fallback can enable this entry. Parent review must bind the
contract digest and current source before isolated qualification/collection.
"""
import asyncio,json
from pathlib import Path
from benchmark_resources import BenchmarkSession,need
PROJECT_MARKER='onit_candidate/voice.py'
RESOURCE_CONTRACT=Path(__file__).resolve().parent/'resource-contract.FINAL.json'
RESOURCE_BINDING=Path(__file__).resolve().parent/'resource-binding.FINAL.json'
def audio_rates():return 16000,48000

async def build(transport,log_dir):
 need(RESOURCE_BINDING.is_file(),'reviewed_resource_contract_not_frozen')
 binding=json.loads(RESOURCE_BINDING.read_bytes())
 need(set(binding)=={'reviewed_execution_approved','resource_contract_sha256'} and binding['reviewed_execution_approved'] is True,'reviewed_resource_contract_not_frozen')
 resource=BenchmarkSession(transport,log_dir,RESOURCE_CONTRACT,binding['resource_contract_sha256'])
 from task_mcp.client import ROOT
 from runtime_bootstrap import install
 install(ROOT,resource.document)
 from onit_candidate import voice
 from me3_assistant.config import load,DEFAULT
 stop=asyncio.Event()
 session=Session(lambda:voice.run(load(DEFAULT),stop_requested=stop,warm_floor=True,validated_delivery=True,benchmark_session=resource),stop,resource)
 return session.run(),session.cancel # Exact pinned launch_assistant.py contract.

def describe():
 if not RESOURCE_CONTRACT.is_file():return {'observation':'explicit ME3 stage observer; contract not frozen'}
 value=json.loads(RESOURCE_CONTRACT.read_bytes())
 return value.get('model_metadata',{})

class Session:
 def __init__(self,start,stop,resource):self.start=start;self.task=None;self.stop=stop;self.resource=resource
 async def run(self):
  from measurement_host import flush
  import sys
  try:return await self._run()
  finally:
   prior=sys.exception()
   errors=[];stages=getattr(self.resource,'stage_observer',None)
   for save in ([stages.finish] if stages is not None else [])+[lambda:flush(self.resource)]:
    try:save()
    except Exception as error:errors.append(error)
   if errors:
    if prior is None:raise errors[0]
    for error in errors:prior.add_note('benchmark measurement persistence failed: '+type(error).__name__)
 async def _run(self):
  need(self.task is None,'one_normal_benchmark_runner_required')
  self.task=asyncio.create_task(self.start())
  try:
   async with asyncio.timeout(90):
    while self.resource.bridge is None or not self.resource.bridge.ready.is_set():
     if self.task.done():await self.task;raise ValueError('benchmark_startup_ended_without_ready')
     await asyncio.sleep(.01)
   need(self.resource.processor.candidate_listening is True,'greeting_tail_and_listening_required')
  except BaseException:
   await self.cancel();raise
  end=asyncio.create_task(self.resource.bridge.peer_end.wait())
  failure=asyncio.create_task(self.resource.bridge.failure.wait())
  try:
   await asyncio.wait({self.task,end,failure},return_when=asyncio.FIRST_COMPLETED)
   if self.resource.bridge.peer_end.is_set() or self.resource.bridge.failure.is_set():await self.cancel()
   await self.task
   need(not self.resource.bridge.failure.is_set(),'digital_transport_failed')
  finally:
   for task in (end,failure):task.cancel()
   await asyncio.gather(end,failure,return_exceptions=True)
 async def cancel(self):
  # Fence new digital writes synchronously at the launcher cancellation edge.
  # Existing failureEvent is never cleared or reclassified.
  if self.resource.bridge is not None:self.resource.bridge.inhibit()
  self.stop.set()
  if self.task is None:return
  # Preserve normal supervise/native/speech cleanup; do not cancel its owner
  # during child settlement. Timeout is unverified, never a closed receipt.
  try:
   async with asyncio.timeout(8):await asyncio.shield(self.task)
  except TimeoutError:raise ValueError('benchmark_normal_cleanup_unverified') from None
  need(self.resource.bridge is not None and self.resource.bridge.cleanup_complete,'benchmark_digital_cleanup_unverified')
