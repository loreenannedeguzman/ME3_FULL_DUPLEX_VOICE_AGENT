"""Frozen completion pipeline plus separate benchmark observations; no provider fallback."""
import asyncio,json,logging,os,sys,time
from pathlib import Path
from dotenv import load_dotenv
from benchmark_m6.runner import Events as BaseEvents
from benchmark_m6.storage import read,atomic,Budget
from benchmark_final.protocol import ROOT,SESSIONS,LIMITS,run_path
from benchmark_final.observe import attach
from me3_completion.integration import apply
from me3_completion.client import ConversationClients
from me3_core.reasoning import CoreReasoning
from speech_mcp.client import ProviderLimited
from task_mcp.client import TaskClient,TaskError
from voice_agent import Settings,configure_pi_audio
class Events(BaseEvents):
    def __init__(self,*args):super().__init__(*args);self.request_trials={}
    def emit(self,event,**data):
        g=data.get('generation',data.get('epoch'));rid=data.get('request_id')
        if event=='core_turn_started' and g is not None:
            trial=self.active
            if trial is None:
                self.incidentals+=1;trial=self.session+'-INC'+str(self.incidentals).zfill(2)
                super().emit('m6_incidental_trial',trial_id=trial,reason='actual speech outside a planned cue')
            self.generations.setdefault(g,trial);self.current_generation=g
        if rid and event in {'mcp_request_start','task_mcp_request_start'}:self.request_trials[rid]=self.generations.get(g,self.active)
        if rid and rid in self.request_trials:data.setdefault('trial_id',self.request_trials[rid])
        super().emit(event,**data)
    def command(self,command):
        if command['action']=='cue':
            c=self.processor.controller
            if c.task is not None and not c.task.done():raise ValueError('previous_actual_work_not_settled')
            if self.processor.pcm or self.processor.speaking or any(not x.done() for x in self.processor.jobs):raise ValueError('input_or_endpoint_not_settled')
        return super().command(command)
class Window:
    def __init__(self,path):self.path=path
    def reserve(self):
        now=time.monotonic();data=read(self.path) if self.path.exists() else [];data=[x for x in data if 0<=now-x<60]
        if len(data)>=14:raise ProviderLimited('local_rate_guard')
        data.append(now);atomic(self.path,data)
async def commands(events,folder):
    offset=0;seen=set();acks={}
    while True:
        path=folder/'commands.jsonl'
        if path.exists():
            with path.open(encoding='utf-8') as f:
                f.seek(offset)
                while True:
                    before=f.tell();line=f.readline()
                    if not line or not line.endswith('\n'):f.seek(before);break
                    offset=f.tell();command=json.loads(line);key=command['command_id']
                    if key in seen:continue
                    seen.add(key)
                    try:acks[key]=events.command(command)
                    except ValueError as exc:acks[key]={'status':'error','reason':str(exc)}
                    atomic(folder/'control-state.json',{'acks':acks,'active':events.active})
        await asyncio.sleep(.05)
async def run(name,session):
    from benchmark_final.console import verify_sources,authorization,harness_source
    verify_sources();authorization(name);folder=run_path(name)/session;manifest=read(run_path(name)/'manifest.json')
    if harness_source()!=manifest['harness']:raise ValueError('benchmark_harness_changed')
    if session not in SESSIONS:raise ValueError('unknown_session')
    if Path('/proc/sys/kernel/random/boot_id').read_text().strip()!=manifest['environment']['boot_id']:raise ValueError('Pi_reboot_requires_new_clock_run')
    events=Events(folder,session);budget=Budget(run_path(name)/'budget.json',LIMITS);window=Window(run_path(name)/'rate.json');app=apply(events);original_build=app.m3.build_pipeline
    def build(*args,**kw):
        result=original_build(*args,**kw);events.processor=result[3];attach(result[3],result[2],events);return result
    class Speech(ConversationClients):
        async def call(self,role,*args,**kw):
            if role=='asr':window.reserve()
            events.emit('m6_budget_reserved',role=role,ordinal=budget.reserve(role));return await super().call(role,*args,**kw)
    class Reasoning(CoreReasoning):
        def __init__(self):super().__init__(events)
        async def answer(self,history):
            window.reserve();ordinal=budget.reserve('reasoning');g=events.current_generation;events.emit('m6_reasoning_request_start',generation=g,ordinal=ordinal,observed_ns=time.monotonic_ns())
            try:
                result=await super().answer(history);events.emit('m6_reasoning_request_end',generation=g,observed_ns=time.monotonic_ns());return result
            except asyncio.CancelledError:events.emit('m6_reasoning_request_cancelled',generation=g,observed_ns=time.monotonic_ns());raise
            except Exception:events.emit('m6_reasoning_request_error',generation=g,observed_ns=time.monotonic_ns());raise
    class Tasks(TaskClient):
        async def call(self,tool,*args,**kw):
            if tool in {'prepare_item','commit_item'} and events.active!='F02':raise TaskError('declared_task_trial_required')
            if tool=='prepare_item' and not (kw.get('item')=='rulers' and type(kw.get('quantity')) is int and kw['quantity']==6):raise TaskError('benchmark_proposal_mismatch')
            if tool=='commit_item':budget.reserve('commit_item')
            return await super().call(tool,*args,**kw)
    app.m3.build_pipeline=build;app.SpeechClients=Speech;app.GoogleSpeech=Reasoning;app.TaskClient=Tasks
    load_dotenv(ROOT/'.env',encoding='utf-8-sig');os.environ.update(TTS_PROVIDER='piper',PIPER_WORKER_MODE='resident',PIPER_PYTHON=str(ROOT/'.venv-piper/bin/python'),PIPER_MODEL=str(ROOT/'models/piper/en_US-ljspeech-high.onnx'),MAX_SESSION_SECONDS='600',M3_MAX_HUMAN_TURNS='8',PIPECAT_SMART_TURN_LOG_DATA='false')
    settings=Settings.load(live=True);configure_pi_audio(settings)
    if settings.output_rate!=manifest['environment']['playback']['rate'] or settings.output_channels!=manifest['environment']['playback']['channels']:raise ValueError('frozen_playback_format_changed')
    events.emit('bf_clock',boot_id=manifest['environment']['boot_id'],clock='time.monotonic_ns',resolution_seconds=time.get_clock_info('monotonic').resolution,acoustic=False)
    watcher=asyncio.create_task(commands(events,folder))
    try:await app.run(settings,events)
    finally:watcher.cancel();await asyncio.gather(watcher,return_exceptions=True);events.emit('bf_session_finished',observed_ns=time.monotonic_ns());events.close()
def build_check():
    from benchmark_final.protocol import PHASE
    os.environ.update(TTS_PROVIDER='piper',PIPER_WORKER_MODE='resident',PIPER_PYTHON=str(ROOT/('.venv-piper/bin/python' if sys.platform=='linux' else '.venv/Scripts/python.exe')),PIPER_MODEL=str(ROOT/'models/piper/en_US-ljspeech-high.onnx'),PIPECAT_SMART_TURN_LOG_DATA='false')
    events=Events(PHASE/('build-'+str(time.monotonic_ns())),'A');app=apply(events);original=app.m3.build_pipeline
    def build(*args,**kw):
        result=original(*args,**kw);events.processor=result[3];attach(result[3],result[2],events);return result
    app.m3.build_pipeline=build
    try:
        settings=Settings.load(live=False);configure_pi_audio(settings);asyncio.run(app.run(settings,events,True));return 0
    finally:events.close()
if __name__=='__main__':
    from loguru import logger
    logger.remove();logging.disable(logging.CRITICAL)
    if sys.argv[1:]==['--build-check']:raise SystemExit(build_check())
    try:asyncio.run(run(sys.argv[1],sys.argv[2]))
    except KeyboardInterrupt:raise SystemExit(130)
    except Exception as exc:print(json.dumps({'status':'FAIL','error_type':type(exc).__name__}),file=sys.stderr);raise SystemExit(2)
