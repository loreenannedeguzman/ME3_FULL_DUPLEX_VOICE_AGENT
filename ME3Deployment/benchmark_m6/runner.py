"""Actual accepted M4 pipeline plus observational wrappers, no synthetic audio."""
import asyncio,json,logging,os,platform,sys,time
from datetime import datetime,timezone
from pathlib import Path
from dotenv import load_dotenv
from benchmark_m6.protocol import LIMITS,TRIALS,get_trial
from benchmark_m6.storage import ROOT,read,write_new,atomic,run_path,Budget
from speech_mcp.diagnostics import StatusEventLog
from voice_agent import Settings,configure_pi_audio
from benchmark_m6.observe import attach

class Events(StatusEventLog):
    def __init__(self,folder,session):
        super().__init__(folder);self.session=session;self.active=None;self.generations={};self.current_generation=0;self.incidentals=0;self.processor=None;self.last_cue_ns=None;self.index=0;self.final=None;self.started=set();self.pending_since=None
    def bind_generation(self,generation):
        if self.active is None:
            self.incidentals+=1;self.active=self.session+'-INC'+str(self.incidentals).zfill(2)
            self.emit('m6_incidental_trial',trial_id=self.active,reason='speech outside planned cue')
        self.generations[generation]=self.active;self.current_generation=generation;self.pending_since=time.monotonic_ns()
    def emit(self,event,**data):
        if event=='response_complete':self.pending_since=None
        self.index+=1;generation=data.get('generation',data.get('epoch'))
        data.setdefault('trial_id',self.generations.get(generation,self.active));data.setdefault('active_trial_id',self.active)
        super().emit(event,session_id=self.session,event_index=self.index,**data)
    def command(self,command):
        action=command['action'];trial=command.get('trial')
        if action=='cue':
            config=get_trial(trial)
            if config['session']!=self.session or self.active is not None or trial in self.started:raise ValueError('trial_not_available')
            now=time.monotonic_ns()
            if self.last_cue_ns is not None and now-self.last_cue_ns<10_000_000_000:raise ValueError('intertrial_interval_under10s')
            self.active=trial;self.started.add(trial);self.last_cue_ns=now;self.final=None
            self.emit('m6_trial_start',trial_id=trial,kind=config['kind'],case=config.get('case'),observed_ns=now)
        elif action=='report':
            if trial!=self.active:raise ValueError('trial_not_active')
            if command.get('outcome') not in {'pass','fail','incomplete','timeout'}:raise ValueError('invalid_outcome')
            allowed={k:command[k] for k in ('outcome','audible','response_correct','overlap','old_stopped','no_replay','note') if k in command}
            for k,v in allowed.items():
                if k not in {'outcome','note'} and type(v) is not bool:raise ValueError('invalid_observation')
            if allowed.get('note') not in {None,'none','wrong_answer','no_reply','unclear_audio','old_returned','split_not_detected','budget','operator_unavailable'}:raise ValueError('invalid_note')
            self.emit('m6_trial_report',trial_id=trial,**allowed);self.final=trial;self.active=None
        elif action=='verification':
            if trial!=self.active or trial!='F02':raise ValueError('task_trial_not_active')
            self.emit('m6_task_external_verification',trial_id=trial,**command['result'])
        else:raise ValueError('unknown_control_action')
        return {'status':'ok','active_trial':self.active}

async def commands(events,folder):
    seen=set();offset=0;acks={}
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
        if events.pending_since and time.monotonic_ns()-events.pending_since>60_000_000_000:
            events.emit('m6_trial_timeout',trial_id=events.active,deadline_seconds=60)
            events.processor.pipeline_failed.set();return
        await asyncio.sleep(.05)

async def run(name,session):
    import m4_voice_agent as app
    from speech_mcp.client import SpeechClients
    from speech_mcp.provider import GoogleSpeech
    from task_mcp.client import TaskClient,TaskError
    folder=run_path(name)/session;events=Events(folder,session);budget=Budget(run_path(name)/'budget.json',LIMITS)
    original_build=app.m3.build_pipeline
    def build(*args,**kwargs):
        runner,worker,transport,processor=original_build(*args,**kwargs);events.processor=processor;attach(processor,transport,events)
        return runner,worker,transport,processor
    class Speech(SpeechClients):
        async def call(self,role,*args,**kwargs):
            ordinal=budget.reserve(role);events.emit('m6_budget_reserved',role=role,ordinal=ordinal)
            return await super().call(role,*args,**kwargs)
    class Reasoning(GoogleSpeech):
        async def answer(self,history):
            ordinal=budget.reserve('reasoning');g=events.current_generation
            events.emit('m6_reasoning_request_start',generation=g,ordinal=ordinal,observed_ns=time.monotonic_ns())
            try:
                result=await super().answer(history)
                events.emit('m6_reasoning_request_end',generation=g,observed_ns=time.monotonic_ns());return result
            except asyncio.CancelledError:
                events.emit('m6_reasoning_request_cancelled',generation=g,observed_ns=time.monotonic_ns());raise
            except Exception:
                events.emit('m6_reasoning_request_error',generation=g,observed_ns=time.monotonic_ns());raise
    class Tasks(TaskClient):
        async def call(self,tool,*args,**kwargs):
            if tool in {'prepare_item','commit_item'} and events.active!='F02':raise TaskError('m6_task_write_trial_required')
            if tool=='commit_item':budget.reserve('commit_item')
            return await super().call(tool,*args,**kwargs)
    app.m3.build_pipeline=build;app.SpeechClients=Speech;app.GoogleSpeech=Reasoning;app.TaskClient=Tasks
    load_dotenv(ROOT/'.env',encoding='utf-8-sig')
    os.environ.update(TTS_PROVIDER='piper',PIPER_WORKER_MODE='resident',PIPER_PYTHON=str(ROOT/'.venv-piper/bin/python'),PIPER_MODEL=str(ROOT/'models/piper/en_US-ljspeech-high.onnx'),MAX_SESSION_SECONDS='600',M3_MAX_HUMAN_TURNS='8')
    settings=Settings.load(live=True);configure_pi_audio(settings)
    events.emit('m6_audio_settings',input_device=settings.input_device,output_device=settings.output_device,output_rate=settings.output_rate,output_channels=settings.output_channels,max_seconds=settings.max_seconds)
    events.emit('m6_clock',implementation=time.get_clock_info('monotonic').implementation,resolution_seconds=time.get_clock_info('monotonic').resolution,clock='monotonic_ns',host=platform.node(),boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip() if platform.system()=='Linux' else None)
    watcher=asyncio.create_task(commands(events,folder))
    try:await app.run(settings,events)
    finally:
        watcher.cancel();await asyncio.gather(watcher,return_exceptions=True)
        if events.active is not None:events.emit('m6_trial_unfinished',trial_id=events.active)
        events.emit('m6_session_finished',observed_ns=time.monotonic_ns());events.close()

def main(name,session):
    from loguru import logger
    logger.remove();logging.disable(logging.CRITICAL)
    try:asyncio.run(run(name,session));return 0
    except KeyboardInterrupt:return 130
    except Exception as exc:print(json.dumps({'status':'FAIL','error_type':type(exc).__name__}),file=sys.stderr);return 2
if __name__=='__main__':raise SystemExit(main(sys.argv[1],sys.argv[2]))
