import asyncio,logging,os,sys,time
from pathlib import Path
from dotenv import load_dotenv
from benchmark_final.runner import Events,commands
from benchmark_final.observe import attach
from benchmark_final.runner import Window
from benchmark_m6.storage import read,Budget
from benchmark_agent.protocol import ROOT,SESSIONS,LIMITS,run_path
from me3_agent.integration import apply
from me3_agent.reasoning import AgentReasoning
from me3_agent.router import AgentRouter
from me3_agent.client import AgentTasks
from me3_agent.speech import AgentSpeechClients
from task_mcp.client import TaskError
from voice_agent import Settings,configure_pi_audio
async def run(name,session):
    from benchmark_agent.console import verify_sources,authorization,harness_source
    verify_sources();authorization(name);manifest=read(run_path(name)/'manifest.json')
    if harness_source()!=manifest['harness']:raise ValueError('benchmark_harness_changed')
    if session not in SESSIONS:raise ValueError('invalid_session')
    if Path('/proc/sys/kernel/random/boot_id').read_text().strip()!=manifest['environment']['boot_id']:raise ValueError('boot_changed_new_run_required')
    folder=run_path(name)/session;events=Events(folder,session);budget=Budget(run_path(name)/'budget.json',LIMITS);window=Window(run_path(name)/'rate.json');app=apply(events);original=app.m3.build_pipeline
    def build(*args,**kwargs):
        result=original(*args,**kwargs);events.processor=result[3];attach(result[3],result[2],events);return result
    class Speech(AgentSpeechClients):
        async def call(self,role,*args,**kw):
            if role=='asr':window.reserve()
            events.emit('m6_budget_reserved',role=role,ordinal=budget.reserve(role));return await super().call(role,*args,**kw)
    class Reasoning(AgentReasoning):
        def __init__(self):super().__init__(events)
        async def decide(self,*args):
            window.reserve();ordinal=budget.reserve('reasoning');g=events.current_generation;step=self.step=str(ordinal)
            events.emit('ba_llm_start',generation=g,step_id=step,ordinal=ordinal,observed_ns=time.monotonic_ns())
            try:result=await super().decide(*args);events.emit('ba_llm_end',generation=g,step_id=step,observed_ns=time.monotonic_ns());return result
            except asyncio.CancelledError:events.emit('ba_llm_cancelled',generation=g,step_id=step,observed_ns=time.monotonic_ns());raise
            except Exception:events.emit('ba_llm_error',generation=g,step_id=step,observed_ns=time.monotonic_ns());raise
    class Router(AgentRouter):
        async def answer(self,history):
            g=self.generation();events.emit('m6_reasoning_request_start',generation=g,observed_ns=time.monotonic_ns())
            try:result=await super().answer(history);events.emit('m6_reasoning_request_end',generation=g,observed_ns=time.monotonic_ns());return result
            except asyncio.CancelledError:events.emit('m6_reasoning_request_cancelled',generation=g,observed_ns=time.monotonic_ns());raise
            except Exception:events.emit('m6_reasoning_request_error',generation=g,observed_ns=time.monotonic_ns());raise
    class Tasks(AgentTasks):
        async def call(self,tool,*args,**kw):
            if tool in {'prepare_item','commit_item'}:raise TaskError('benchmark_readonly_no_proposals_or_writes')
            return await super().call(tool,*args,**kw)
    app.m3.build_pipeline=build;app.GoogleSpeech=Reasoning;app.TaskReasoner=Router;app.TaskClient=Tasks;app.SpeechClients=Speech
    load_dotenv(ROOT/'.env',encoding='utf-8-sig');os.environ.update(TTS_PROVIDER='piper',PIPER_WORKER_MODE='resident',PIPER_PYTHON=str(ROOT/'.venv-piper/bin/python'),PIPER_MODEL=str(ROOT/'models/piper/en_US-ljspeech-high.onnx'),MAX_SESSION_SECONDS='600',M3_MAX_HUMAN_TURNS='8',PIPECAT_SMART_TURN_LOG_DATA='false')
    settings=Settings.load(live=True);configure_pi_audio(settings)
    events.emit('ba_configuration',controlled_utility_delay_ms=0,controlled_asr_timeout=False,task_write_budget=0,source_revision=manifest['source']['revision'],clock='Pi time.monotonic_ns',boot_id=manifest['environment']['boot_id'])
    watcher=asyncio.create_task(commands(events,folder))
    try:await app.run(settings,events)
    finally:watcher.cancel();await asyncio.gather(watcher,return_exceptions=True);events.emit('ba_session_finished');events.close()
def build_check():
    from benchmark_agent.protocol import PHASE
    from me3_core.events import CoreEvents
    events=CoreEvents(PHASE/('build-'+str(time.monotonic_ns())));app=apply(events)
    os.environ.update(TTS_PROVIDER='piper',PIPER_WORKER_MODE='resident',PIPER_PYTHON=str(ROOT/('.venv-piper/bin/python' if sys.platform=='linux' else '.venv/Scripts/python.exe')),PIPER_MODEL=str(ROOT/'models/piper/en_US-ljspeech-high.onnx'),PIPECAT_SMART_TURN_LOG_DATA='false')
    try:settings=Settings.load(live=False);configure_pi_audio(settings);asyncio.run(app.run(settings,events,True));return 0
    finally:events.close()
if __name__=='__main__':
    from loguru import logger
    logger.remove();logging.disable(logging.CRITICAL)
    if sys.argv[1:]==['--build-check']:raise SystemExit(build_check())
    try:asyncio.run(run(sys.argv[1],sys.argv[2]))
    except KeyboardInterrupt:raise SystemExit(130)
