"""Bounded opt-in live runner; fresh shared caps, no raw speech retention."""
import argparse,asyncio,json,logging,os,re,sys,time
from pathlib import Path
from dotenv import load_dotenv
from voice_agent import ROOT,Settings,configure_pi_audio
from me3_core.events import CoreEvents
from benchmark_m6.storage import read,write_new,Budget,atomic
from me3_agent.integration import apply
from me3_agent.reasoning import AgentReasoning
from me3_agent.client import AgentTasks
from me3_agent.speech import AgentSpeechClients
from speech_mcp.client import ProviderLimited
from task_mcp.client import TaskError
PHASE=ROOT/'evidence/completion/autonomous-20261020-001'
LIMITS={'asr':72,'reasoning':64,'tts':102,'commit_item':1}
class Events(CoreEvents):
    def emit(self,event,**data):
        super().emit(event,**data)
        if event in {'agent_tool_selected','agent_tool_decision_rejected','agent_tool_failure_recovered','task_confirmation_requested','task_independently_verified','turn_error','session_start','session_end'}:
            print(json.dumps({'event':event,**{k:v for k,v in data.items() if k in {'generation','tool','reason','error_type','stop_reason'}}}),flush=True)
def check_sources():
    import hashlib
    source=read(PHASE/'candidate-source.json')
    if any(hashlib.sha256((ROOT/n).read_bytes()).hexdigest()!=h for n,h in source['source_hashes'].items()):raise ValueError('candidate_source_changed')
    return source
async def controls(events):
    reader=asyncio.StreamReader();protocol=asyncio.StreamReaderProtocol(reader)
    transport,_=await asyncio.get_running_loop().connect_read_pipe(lambda:protocol,sys.stdin)
    try:await read_controls(events,reader)
    finally:transport.close()
async def read_controls(events,reader):
    while True:
        line=await reader.readline()
        if not line:raise EOFError('operator_channel_closed')
        try:
            cmd=json.loads(line);action=cmd.get('action');controller=events.processor.controller if events.processor else None
            if action=='status':
                print(json.dumps({'status':{'active':events.active,'counts':dict(events.counts),'budget':read(PHASE/'budget.json'),'generation':controller.clock.current if controller else None,'settled':bool(controller and (controller.task is None or controller.task.done()))}}),flush=True)
            elif action=='label':
                if not isinstance(cmd.get('trial'),str) or not re.fullmatch('[A-Za-z0-9_-]{1,40}',cmd['trial']):raise ValueError('invalid_trial')
                if controller and controller.task and not controller.task.done():raise ValueError('work_not_settled')
                events.active=cmd['trial'];events.emit('agent_trial_start',trial=events.active);print(json.dumps({'trial_ready':events.active}),flush=True)
            elif action=='report':
                if not events.active or cmd.get('outcome') not in {'pass','fail','incomplete','timeout'}:raise ValueError('invalid_report')
                events.emit('agent_operator_report',trial=events.active,outcome=cmd['outcome'],observation=cmd.get('observation','')[:1200],source='direct human report');print(json.dumps({'report_recorded':events.active}),flush=True);events.active=None
            elif action=='arm_asr_timeout':
                if not controller or controller.task and not controller.task.done():raise ValueError('work_not_settled')
                marker=events.path.parent/'controlled-asr-timeout-armed.json'
                if marker.exists():raise ValueError('controlled_timeout_already_armed_this_session')
                with marker.open('x',encoding='utf-8') as f:json.dump({'deadline_ms':100,'one_shot':True,'not_natural_provider_outage':True},f)
                events.speech.arm_controlled_timeout();events.emit('agent_controlled_asr_timeout_armed');print(json.dumps({'asr_timeout_armed':True}),flush=True)
            elif action=='stop':
                events.emit('agent_operator_stop');return
            else:raise ValueError('invalid_control')
        except ValueError as exc:print(json.dumps({'control_error':str(exc)}),flush=True)
async def run(session,build=False,utility_delay_ms=0):
    if type(utility_delay_ms) is not int or not 0<=utility_delay_ms<=20000:raise ValueError('invalid_controlled_delay')
    if not re.fullmatch('(?:live|build)[0-9]{3}',session):raise ValueError('invalid_session')
    if not build:
        auth=read(PHASE/'authorization.json')
        if auth.get('approved') is not True or auth.get('existing_google_allowance_confirmed') is not True or auth.get('limits')!=LIMITS:raise ValueError('fresh_authorization_required')
        check_sources()
    folder=PHASE/'sessions'/session;events=Events(folder);app=apply(events);budget=Budget(PHASE/'budget.json',LIMITS)
    def reserve_wire():
        path=PHASE/'rate.json';now=time.monotonic();data=read(path) if path.exists() else [];data=[x for x in data if 0<=now-x<60]
        if len(data)>=14:raise ProviderLimited('local_rate_guard')
        data.append(now);atomic(path,data)
    class Speech(AgentSpeechClients):
        def __init__(self,*args,**kwargs):super().__init__(*args,**kwargs);events.speech=self
        async def call(self,role,*args,**kwargs):
            if role=='asr':reserve_wire()
            events.emit('agent_budget_reserved',role=role,ordinal=budget.reserve(role))
            return await super().call(role,*args,**kwargs)
    class Reasoning(AgentReasoning):
        def __init__(self):super().__init__(events)
        async def decide(self,*args):
            reserve_wire();events.emit('agent_budget_reserved',role='reasoning',ordinal=budget.reserve('reasoning'))
            return await super().decide(*args)
    class Tasks(AgentTasks):
        def __init__(self,*args,**kwargs):super().__init__(*args,utility_delay_ms=utility_delay_ms,**kwargs)
        async def call(self,tool,generation,**kwargs):
            if tool=='prepare_item' and not (kwargs.get('item')=='rulers' and type(kwargs.get('quantity')) is int and kwargs['quantity']==6):
                events.emit('agent_live_write_scope_rejected',generation=generation,write_executed=False);raise TaskError('authorized_trial_item_required')
            if tool=='commit_item':
                events.emit('agent_qualification_write_rejected',generation=generation,write_executed=False)
                raise TaskError('qualification_is_read_only')
            return await super().call(tool,generation,**kwargs)
    app.GoogleSpeech=Reasoning;app.SpeechClients=Speech;app.TaskClient=Tasks
    original=app.m3.build_pipeline
    def build_pipeline(*args,**kw):
        result=original(*args,**kw);events.processor=result[3];speak=result[3].controller.speak
        async def spoken(text,*a,**k):
            if k.get('greeting'):text='I am ready. We can talk, check the time, calculate, or prepare a shopping-list item for your confirmation.'
            return await speak(text,*a,**k)
        result[3].controller.speak=spoken;return result
    app.m3.build_pipeline=build_pipeline
    load_dotenv(ROOT/'.env',encoding='utf-8-sig');os.environ.update(TTS_PROVIDER='piper',PIPER_WORKER_MODE='resident',PIPER_PYTHON=str(ROOT/('.venv-piper/bin/python' if sys.platform=='linux' else '.venv/Scripts/python.exe')),PIPER_MODEL=str(ROOT/'models/piper/en_US-ljspeech-high.onnx'),MAX_SESSION_SECONDS='600',M3_MAX_HUMAN_TURNS='8',PIPECAT_SMART_TURN_LOG_DATA='false')
    settings=Settings.load(live=not build);configure_pi_audio(settings)
    events.emit('agent_configuration',limits=LIMITS,model_commit_exposed=False,tool_round_limit=3,controlled_utility_delay_ms=utility_delay_ms,raw_audio_or_transcripts_retained=False)
    task=asyncio.create_task(app.run(settings,events,build))
    watcher=None if build else asyncio.create_task(controls(events))
    try:
        if watcher:
            done,_=await asyncio.wait([task,watcher],return_when=asyncio.FIRST_COMPLETED)
            if watcher in done:
                try:await watcher
                finally:task.cancel()
        await task
    except asyncio.CancelledError:
        events.emit('agent_session_canceled_cleanly')
    finally:
        if watcher:watcher.cancel();await asyncio.gather(watcher,return_exceptions=True)
        events.close()
def main():
    p=argparse.ArgumentParser();p.add_argument('--session',required=True);p.add_argument('--build-check',action='store_true');p.add_argument('--utility-delay-ms',type=int,default=0);a=p.parse_args()
    from loguru import logger
    logger.remove();logging.disable(logging.CRITICAL)
    try:asyncio.run(run(a.session,a.build_check,a.utility_delay_ms));return 0
    except KeyboardInterrupt:return 130
    except Exception as exc:print(json.dumps({'status':'FAIL','error_type':type(exc).__name__}),flush=True);return 2
if __name__=='__main__':raise SystemExit(main())
