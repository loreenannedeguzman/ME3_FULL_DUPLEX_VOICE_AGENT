"""Actual mic/output functional runner with bounded shared request budget."""
import asyncio,json,logging,os,re,sys,time
from pathlib import Path
from dotenv import load_dotenv
from benchmark_m6.storage import ROOT,read,write_new,atomic,Budget
from me3_completion.scenarios import SESSIONS,LIMITS
from me3_core.events import CoreEvents
from me3_completion.integration import apply
from me3_completion.client import ConversationClients as CoreSpeechClients
from me3_core.reasoning import CoreReasoning
from speech_mcp.client import ProviderLimited
from task_mcp.client import TaskClient,TaskError
from voice_agent import Settings,configure_pi_audio

def run_path(name):
    if not re.fullmatch(r'functional-[0-9]{8}-[0-9]{3}',name):raise ValueError('invalid_core_run')
    return ROOT/'evidence/completion/final-20261020-001'/name
PHASE=ROOT/'evidence/completion/final-20261020-001'
from me3_core.live_runner import resources
class Window:
    def __init__(self,events):self.events=events
    def reserve(self):
        path=PHASE/'functional-rate.json';now=time.monotonic();data=read(path) if path.exists() else []
        data=[t for t in data if 0<=now-t<60]
        if len(data)>=14:
            self.events.emit('completion_local_rate_guard',automatic_retry=False);raise ProviderLimited('local_rate_guard')
        data.append(now);atomic(path,data)

async def commands(events,folder,speech,budget):
    seen=set();offset=0;acks={}
    while True:
        path=folder/'commands.jsonl'
        if path.exists():
            with path.open(encoding='utf-8') as f:
                f.seek(offset)
                while True:
                    before=f.tell();line=f.readline()
                    if not line or not line.endswith('\n'):f.seek(before);break
                    offset=f.tell();cmd=json.loads(line);key=cmd['command_id']
                    if key in seen:continue
                    seen.add(key)
                    try:
                        action=cmd['action'];episode=cmd.get('episode');controller=events.processor.controller
                        if action=='cue':
                            if episode not in SESSIONS[folder.name] or episode in events.started or events.active is not None:raise ValueError('episode_not_available')
                            if controller.task is not None and not controller.task.done():raise ValueError('previous_work_not_settled')
                            if events.processor.pcm or events.processor.speaking or any(not x.done() for x in events.processor.jobs):raise ValueError('input_or_endpoint_not_settled')
                            events.active=episode;events.started.add(episode);events.emit('core_episode_start',episode_id=episode,**SESSIONS[folder.name][episode])
                        elif action=='end_episode':
                            if events.active!=episode:raise ValueError('episode_not_active')
                            events.emit('core_episode_end',episode_id=episode);events.active=None
                        elif action=='arm_timeout':
                            if events.active!='D1' or controller.task is not None and not controller.task.done():raise ValueError('timeout_trial_not_ready')
                            budget.reserve('controlled_timeout');speech.arm_controlled_timeout()
                        else:raise ValueError('invalid_control')
                        acks[key]={'status':'ok','active_episode':events.active}
                    except ValueError as exc:acks[key]={'status':'error','reason':str(exc)}
                    atomic(folder/'control-state.json',{'acks':acks,'active':events.active,'generation':events.current_generation})
        await asyncio.sleep(.05)

async def run(name,session):
    if session not in SESSIONS:raise ValueError('invalid_session')
    folder=run_path(name)/session;events=CoreEvents(folder);app=apply(events);budget=Budget(PHASE/'functional-budget.json',LIMITS);window=Window(events);original_run=app.run;holder={}
    class Speech(CoreSpeechClients):
        def __init__(self,*args,**kwargs):super().__init__(*args,**kwargs);holder['speech']=self
        async def call(self,role,*args,**kwargs):
            if role=='asr':window.reserve()
            ordinal=budget.reserve(role);events.emit('core_budget_reserved',role=role,ordinal=ordinal)
            return await super().call(role,*args,**kwargs)
    class Reasoning(CoreReasoning):
        def __init__(self):super().__init__(events)
        async def answer(self,history):
            window.reserve();ordinal=budget.reserve('reasoning');g=events.current_generation;events.emit('core_reasoning_start',generation=g,ordinal=ordinal)
            try:return await super().answer(history)
            finally:events.emit('core_reasoning_terminal',generation=g)
    class Tasks(TaskClient):
        async def call(self,tool,*args,**kw):
            if tool in {'prepare_item','commit_item'} and events.active not in {'D4','D5','E1','E2'}:raise TaskError('declared_confirmed_task_trial_required')
            if tool=='prepare_item':
                from me3_core.task import ProposalMismatch
                expected=3 if events.active=='D4' else 4
                ok=kw.get('item')=='markers' and type(kw.get('quantity')) is int and kw['quantity']==expected
                events.emit('completion_trial_proposal_validation',generation=args[0],matches_expected=ok,payload_retained=False)
                if not ok:raise ProposalMismatch('trial_proposal_mismatch')
            if tool=='commit_item' and events.active!='E2':raise TaskError('explicit_final_confirmation_episode_required')
            if tool=='commit_item':budget.reserve('commit_item')
            return await super().call(tool,*args,**kw)
    app.SpeechClients=Speech;app.GoogleSpeech=Reasoning;app.TaskClient=Tasks
    load_dotenv(ROOT/'.env',encoding='utf-8-sig');os.environ.update(TTS_PROVIDER='piper',PIPER_WORKER_MODE='resident',PIPER_PYTHON=str(ROOT/'.venv-piper/bin/python'),PIPER_MODEL=str(ROOT/'models/piper/en_US-ljspeech-high.onnx'),MAX_SESSION_SECONDS='600',M3_MAX_HUMAN_TURNS='8',PIPECAT_SMART_TURN_LOG_DATA='false')
    settings=Settings.load(live=True);configure_pi_audio(settings);events.emit('core_runtime',session=session,mode='focused functional validation, not latency benchmark',monotonic='time.monotonic_ns',boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),raw_audio_or_transcripts_retained=False)
    async def watch():
        while events.processor is None or 'speech' not in holder:await asyncio.sleep(.05)
        await commands(events,folder,holder['speech'],budget)
    watcher=asyncio.create_task(watch());monitor=asyncio.create_task(resources(events))
    try:await original_run(settings,events)
    finally:
        watcher.cancel();monitor.cancel();await asyncio.gather(watcher,monitor,return_exceptions=True);events.emit('core_session_finished',observed_ns=time.monotonic_ns());events.close()
if __name__=='__main__':
    from loguru import logger
    logger.remove();logging.disable(logging.CRITICAL)
    try:asyncio.run(run(sys.argv[1],sys.argv[2]))
    except KeyboardInterrupt:raise SystemExit(130)
    except Exception as exc:print(json.dumps({'status':'FAIL','error_type':type(exc).__name__}),file=sys.stderr);raise SystemExit(2)
