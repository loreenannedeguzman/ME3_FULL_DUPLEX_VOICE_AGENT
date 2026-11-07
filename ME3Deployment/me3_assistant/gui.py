"""Loopback-only visual console. Standby never opens audio, MCP, or a model."""
import argparse,asyncio,hashlib,json,logging,secrets,sys,time,uuid,threading
from datetime import datetime,timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from me3_assistant.config import load,DEFAULT
from me3_assistant.guard import NORMAL,PHASE,KEYS,authorize,verify_sources,SharedBudget,lock
from task_mcp.client import ROOT

WEB=Path(__file__).with_name('web')

def row(role,text):return {'type':'user_input' if role=='user' else 'model_output','content':[{'type':'text','text':text}]}

def conversation(history):
    return [{'id':str(i),'role':'user' if r['type']=='user_input' else 'assistant',
             'text':' '.join(p.get('text','') for p in r.get('content',[]) if p.get('type')=='text')}
            for i,r in enumerate(history) if not r.get('control_state') and r.get('type') in {'user_input','model_output'}][-64:]

class Console:
    def __init__(self,config,source,reminders=None):
        from me3_assistant.youtube import PlayerBridge
        from me3_assistant.youtube_gui import voice_active
        self.youtube=PlayerBridge(lambda:voice_active(self))
        self.reminders=reminders
        self.voice_grant_binding=None;self.failed_voice_grant=None;self.backend='google'
        self.voice_failure=None
        self.config=config;self.source=source;self.mode='standby';self.state='stopped';self.notice='Choose a session when allowance and readiness are confirmed.'
        self.tasks={};self.public_error=None;self.voice_replies=[]
        self._display_gate_lock=threading.Lock();self._display_gate_cache=None
        self.task=None;self.turn=None;self.stop_event=None;self.ready=None;self.controller=None;self.router=None;self.events=[];self.history=[];self.pending=None;self.generation=0;self.turns=0
    def bind_controller(self,controller):
        self.controller=controller;owned={}
        def display(generation,text,state):
            record=owned.get(generation)
            if record is None:
                anchor=next((r for r in reversed(controller.history) if r.get('type')=='user_input'),None)
                if anchor is None:return
                record={'anchor':anchor,'text':text,'state':state}
                owned[generation]=record;self.voice_replies=(self.voice_replies+[record])[-32:]
            record.update(text=text,state=state)
        controller.display_reply=display
    def display_conversation(self,history):
        # Anchor by row identity, not text/generation (which can repeat).
        # These projections never enter the model's history or durable evidence.
        records={id(r['anchor']):r for r in self.voice_replies};messages=[];projected=False
        for i,r in enumerate(history):
            if r.get('type')=='user_input':projected=False
            if r.get('control_state'):continue
            if r.get('type')=='model_output' and projected:continue
            if r.get('type') not in {'user_input','model_output'}:continue
            messages.extend({**m,'id':str(i)} for m in conversation([r]))
            record=records.get(id(r))
            if record is not None:
                messages.append({'id':str(i)+'-reply','role':'assistant','text':record['text'],'state':record['state']})
                projected=True
        return messages[-64:]
    def observe(self,event,**fields):
        # Explicit projection: never expose tool arguments, credentials or raw events.
        entry={'event':event,'time':datetime.now(timezone.utc).isoformat(),'generation':fields.get('generation',fields.get('epoch'))}
        for key in ('tool','server','role','reason','state','ordinal'):
            if isinstance(fields.get(key),(str,int,bool)):entry[key]=fields[key]
        self.events=(self.events+[entry])[-40:]
        if event=='speaker_aec_fault':self.voice_failure='audio'
        elif event=='groq_admission_denied':self.voice_failure='capacity'
        if event.startswith('assistant_rpc_') or event=='assistant_goal_progress':
            key=fields.get('goal_id') or fields.get('request_id')
            states={'assistant_rpc_start':'running','assistant_rpc_end':'received','assistant_rpc_error':'failed','assistant_rpc_canceled':'canceled'}
            if key:
                tool=fields.get('tool','Task');label=tool.split('__')[-1].replace('_',' ').capitalize()
                if fields.get('ordinal'):label='Step '+str(fields['ordinal'])+' · '+label
                self.tasks[key]={'label':label,'state':fields.get('state',states.get(event,'running'))}
                self.tasks=dict(list(self.tasks.items())[-12:])
        if event in {'turn_error','assistant_tool_failed','assistant_rpc_error','assistant_reasoning_recoverable_failure','core_turn_deadline'}:
            self.public_error='I could not finish this request. Completed actions remain unchanged. Please check before continuing.'
            if self.mode=='voice' and self.task and not self.task.done() and self.stop_event and not self.stop_event.is_set():
                self.stop_event.set();asyncio.create_task(self.stop())
        if self.mode=='voice' and self.stop_event and self.stop_event.is_set():
            # The owner can emit a final settled/listening state during cleanup.
            # Keep its closing latch until the actual voice owner has exited.
            self.state=('stopped' if event=='gui_voice_closed' or not self.task or self.task.done() else 'stopping')
            if event=='assistant_voice_stop_requested':self.notice='Stopping conversation…'
            return
        if event=='assistant_speech_preparing':self.state='preparing_speech'
        elif event=='groq_capacity_wait_start':self.state='waiting_capacity'
        elif event=='groq_capacity_wait_end':self.state='processing'
        elif event=='completion_ownership':self.state=fields.get('current','listening')
        elif event in {'generation_playout_started','core_first_submission'}:self.state='speaking'
        elif event=='core_turn_started':self.state='processing'
        elif event=='core_turn_settled':self.state='listening'
        elif event in {'turn_error','assistant_tool_failed'}:self.notice='A request failed. See activity; no automatic retry.'
        elif event=='assistant_reasoning_recoverable_failure':self.notice='The reasoning service could not finish this turn. No automatic retry; you can give a new request.'
    def gate(self,mode,backend=None):
        try:
            source=verify_sources()
            if source['revision']!=self.source['revision']:raise ValueError('restart_console_after_source_change')
            grant,limits=authorize(self.config,source,grant_path=NORMAL/('text-authorization.json' if mode=='text' else 'authorization.json'),mode=mode,backend=backend)
            binding=hashlib.sha256(json.dumps(grant,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            if mode=='normal' and grant.get('profile')!='standing' and binding==self.failed_voice_grant:raise ValueError('previous_voice_failure_requires_fresh_authorization')
            if sys.platform!='linux':raise ValueError('live_sessions_require_Pi')
            return grant,limits
        except FileNotFoundError:raise ValueError('fresh_session_authorization_required') from None
    def status_checks(self):
        # Worker-thread work reads files and immutable configuration only.
        usage=json.loads((PHASE/'budget.json').read_text(encoding='utf-8-sig'));limits=json.loads((PHASE/'authorization.json').read_text(encoding='utf-8-sig'))['limits']
        signatures=[]
        for name in ['operating-authorization.json','authorization.json','text-authorization.json','candidate-source.json']:
            try:s=(NORMAL/name).stat();signatures.append((name,s.st_mtime_ns,s.st_size))
            except OSError:signatures.append((name,None,None))
        key=(tuple(signatures),self.failed_voice_grant)
        # Cache only the display gate for2s, not counters, messages or actions.
        # Changed approval files invalidate immediately; every start and runtime
        # reservation still validates fresh authority independently of this view.
        with self._display_gate_lock:
            cached=self._display_gate_cache
            if cached and cached[0]==key and time.monotonic()<cached[1]:gates=cached[2]
            else:
                gates={}
                for mode in ('normal','text'):
                    try:
                        grant,_=self.gate(mode);gates[mode]={'ready':True,'reason':'Authorized','backend':grant.get('backend','google')}
                    except Exception as exc:gates[mode]={'ready':False,'reason':str(exc) if type(exc) is ValueError else 'Source or authorization check failed'}
                self._display_gate_cache=(key,time.monotonic()+2,gates)
        try:reminder_state=self.reminders.listing() if self.reminders else {'available':False,'scheduler_active':False,'reminders':[]}
        except Exception:reminder_state={'available':False,'scheduler_active':False,'reminders':[],'error':'local_reminder_store_unavailable'}
        return usage,limits,gates,reminder_state
    async def read_state(self):
        checks=await asyncio.to_thread(self.status_checks)
        return self.snapshot(checks)
    def snapshot(self,checks=None):
        usage,limits,gates,reminder_state=checks if checks is not None else self.status_checks()
        # Live controller/history/pending state is projected on its owner loop,
        # without awaits or concurrent worker reads during mode switches/Stop.
        controller=self.controller;pending=self.pending
        history=list(controller.history if controller is not None else self.history)
        messages=self.display_conversation(history)
        if pending:messages.append({'id':pending['id'],'role':'assistant','text':pending['text']})
        return {'backend':self.backend,'backend_choices':['google','groq'],'reminders':reminder_state,'mode':self.mode,'state':self.state,'notice':self.notice,'source_revision':self.source['revision'],'config_sha256':self.config['sha256'],
                'youtube':self.youtube.public(),'tasks':list(self.tasks.values()),'error':self.public_error,'messages':messages,'pending_display':pending['id'] if pending else None,'events':self.events,
                'operating_profile':self.source.get('operating_profile','qualification'),'budget':{k:{'used':usage[k],'limit':limits[k],'remaining':max(0,limits[k]-usage[k])} for k in sorted(KEYS)},
                'gates':gates,'active':self.task is not None and not self.task.done(),'qualification':'PARTIAL_PASS; GUI is not acoustic acceptance',
                'can_send':self.mode=='text' and self.state=='ready' and pending is None}
    async def command(self,data):
        action=data.get('action')
        if action=='youtube_poll':return self.youtube.poll(data.get('client'))
        if action=='youtube_report':return self.youtube.report(data.get('client'),data.get('packet'))
        if action=='youtube_validate':
            from me3_assistant.youtube import video_id
            return {'video_id':video_id(data.get('url'))}
        if action=='ack_reminder':
            if not self.reminders or not self.reminders.acknowledge(data.get('reminder_id')):raise ValueError('due_reminder_not_found')
        elif action in {'start_voice','start_text'}:
            if self.task and not self.task.done():raise ValueError('stop_current_session_first')
            mode='normal' if action=='start_voice' else 'text'
            if mode=='normal' and self.youtube.public()['state'] in {'playing','buffering'}:raise ValueError('Pause YouTube before starting voice; browser-audio echo cancellation is not qualified yet.')
            grant,limits=await asyncio.to_thread(self.gate,mode,backend=data.get('backend'))
            from me3_assistant.backends import selected,check_ready
            self.backend=selected(grant,data.get('backend'));check_ready(self.backend)
            if mode=='normal':self.voice_grant_binding=hashlib.sha256(json.dumps(grant,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            self.public_error=None;self.voice_failure=None;self.pending=None;self.turns=0;self.controller=None;self.stop_event=asyncio.Event();self.ready=asyncio.Event()
            self.mode='voice' if mode=='normal' else 'text';self.state='starting';self.notice='Opening the authorized Pi session.'
            self.task=asyncio.create_task(self.voice() if mode=='normal' else self.text(grant,limits))
        elif action=='send':
            text=data.get('text')
            if not isinstance(text,str) or not 1<=len(text.strip())<=4000:raise ValueError('message_must_be_1_to_4000_characters')
            if self.mode!='text' or self.state!='ready' or self.pending is not None or not self.ready or not self.ready.is_set():raise ValueError('text_session_not_ready')
            if self.turns>=self.config['runtime']['max_turns']:raise ValueError('configured_turn_limit')
            self.generation+=1;self.turns+=1;self.history=(self.history+[row('user',text.strip())])[-16:];self.state='processing';self.notice='Processing typed request. No speech synthesis.'
            self.turn=asyncio.create_task(self.answer(self.generation))
        elif action=='ack':
            if self.pending and data.get('id')==self.pending['id']:
                self.history=(self.history+[row('assistant',self.pending['text'])])[-16:];self.pending=None;self.state='ready';self.notice='Answer displayed. Ready for the next typed request.'
        elif action=='cancel':
            if self.mode!='text':raise ValueError('use_stop_session_for_voice')
            await self.cancel();self.state='ready';self.notice='Canceled pending typed work. Completed actions were not undone.'
        elif action=='stop':await self.stop()
        elif action=='clear':
            if self.task and not self.task.done():raise ValueError('stop_before_clearing_display')
            self.history=[];self.voice_replies=[];self.tasks={};self.public_error=None;self.controller=None;self.pending=None;self.notice='In-memory conversation cleared. Evidence and counters are preserved.'
        else:raise ValueError('unknown_command')
        return {'ok':True}
    async def cancel(self):
        self.generation+=1;self.pending=None
        if self.router:self.router.pending=None
        if self.turn and not self.turn.done():self.turn.cancel();await asyncio.gather(self.turn,return_exceptions=True)
        self.turn=None
    async def stop(self):
        if self.stop_event:self.stop_event.set()
        if self.task and not self.task.done():
            self.state='stopping'
            # The owner exits its own MCP/AnyIO contexts after ordered pipeline
            # shutdown. Concurrent Stop/poll timeout must not cancel that owner.
            await asyncio.shield(asyncio.gather(self.task,return_exceptions=True))
        self.state='stopped';self.notice='Session stopped; microphone and model work closed.'
    async def voice(self):
        from me3_assistant.runner import run
        try:await run(self.config,observer=self.observe,on_controller=self.bind_controller,backend=self.backend,initial_history=list(self.history),stop_requested=self.stop_event)
        except asyncio.CancelledError:pass
        except Exception as exc:
            self.public_error={'audio':'Voice stopped because microphone or speaker audio was interrupted. Check the Pi audio before starting again.','capacity':'Voice stopped because service capacity is unavailable. Wait for capacity before starting again.'}.get(self.voice_failure,'Voice stopped before the request finished. Check its status before starting again.')
            self.failed_voice_grant=self.voice_grant_binding
            reason='audio_route_unavailable' if type(exc) is ValueError and str(exc).startswith('Project ALSA route ') else 'startup_or_runtime_failure'
            self.notice='Voice session ended: '+type(exc).__name__+'; '+reason+'. A fresh authorization is required before another start.'
            self.observe('gui_voice_failed',reason=reason)
            path=NORMAL/'gui-failures.jsonl';path.parent.mkdir(parents=True,exist_ok=True)
            if not path.exists() or path.stat().st_size<2_000_000:
                with path.open('a',encoding='utf-8') as f:f.write(json.dumps({'time':datetime.now(timezone.utc).isoformat(),'source_revision':self.source['revision'],'event':'gui_voice_failed','error_type':type(exc).__name__,'reason':reason})+'\n')
        finally:
            if self.controller:self.history=list(self.controller.history);self.controller=None
            self.state='stopped'
            if self.stop_event and self.stop_event.is_set() and not self.public_error:self.notice='Conversation stopped; microphone closed.'
            self.observe('gui_voice_closed')
    async def text(self,grant,limits):
        # This owner task enters AND exits MCP's AnyIO context. Requests are child tasks.
        from dotenv import load_dotenv
        from me3_assistant.registry import Registry
        from me3_assistant.router import Router
        from me3_assistant.reasoning import Reasoning
        from me3_assistant.actions import Journal
        from me3_assistant.runner import Events
        name='text-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]
        events=None;model=None;music_output=None;music_monitor=None
        try:
            with lock(NORMAL/'session.lock'):
                from me3_assistant.backends import selected,check_ready,reasoning_class
                backend=selected(grant);check_ready(backend)
                events=Events(NORMAL/'sessions'/name,observer=self.observe)
                if backend=='google':load_dotenv(ROOT/'.env',encoding='utf-8-sig')
                budget=SharedBudget(grant,limits)
                class Model(reasoning_class(backend)):
                    async def decide(inner,*args):
                        events.current_generation=self.generation
                        events.emit('assistant_budget_reserved',generation=self.generation,role='reasoning',ordinal=budget.reserve('reasoning'))
                        return await super().decide(*args)
                model=Model(events)
                from me3_assistant.standing_music import authorized as routine_music
                if routine_music(grant):
                    from me3_assistant.typed_music import TypedMusicOutput
                    music_output=TypedMusicOutput(events.path.parent/'music-bus.pcm',events)
                    async def watch_music():
                        while not self.stop_event.is_set():
                            if music_output.fault.is_set():
                                self.public_error='Local music output stopped. Check the Pi audio before starting again.'
                                events.emit('assistant_typed_music_output_failed',automatic_retry=False);self.stop_event.set();return
                            await asyncio.sleep(.05)
                    music_monitor=asyncio.create_task(watch_music())
                async with Registry(events,self.config,**({'music_bus':music_output.bus.path} if music_output else {})) as registry:
                    if music_output:registry.ensure_music_output=music_output.ensure_open
                    registry.budget=budget;registry.journal=Journal(ROOT/'data/assistant-actions.jsonl');registry.allowed_actions=grant.get('allowed_actions',[]);registry.session_grant=grant
                    self.router=Router(model,registry,events)
                    owner=self
                    # Router expects controller.current(g) and controller.clock.current.
                    class Clock:
                        @property
                        def current(inner):return owner.generation
                    class Control:
                        clock=Clock()
                        def current(inner,g):return g==owner.generation and not owner.stop_event.is_set()
                    self.router.controller=Control();self.state='ready';self.ready.set();self.notice=('Typed session ready. Pi artifact workspace and separately authorized service controls available.' if grant.get('workspace_access') is True else 'Typed session ready. Local music controls available.' if music_output else 'Typed session ready. Read-only tools; no ASR, TTS or task writes.')
                    events.emit('gui_text_start',source_revision=self.source['revision'],config_sha256=self.config['sha256'],backend=backend)
                    try:
                        async with asyncio.timeout(self.config['runtime']['max_session_seconds']):await self.stop_event.wait()
                    except TimeoutError:self.notice='Configured session duration reached.'
                    finally:await self.cancel()
        except Exception as exc:
            self.public_error='The text conversation stopped. Check the connection before starting again.'
            self.notice='Typed session ended: '+type(exc).__name__+'; no automatic retry.'
        finally:
            self.router=None;self.state='stopped'
            if music_monitor:
                music_monitor.cancel();await asyncio.gather(music_monitor,return_exceptions=True)
            if music_output:await music_output.aclose()
            if model:await model.close()
            if events:events.emit('gui_text_closed');events.close()
    async def answer(self,g):
        try:
            async with asyncio.timeout(60):answer=await self.router.answer(list(self.history))
            if g==self.generation and not self.stop_event.is_set():
                self.pending={'id':uuid.uuid4().hex,'text':answer};self.state='awaiting_display'
        except asyncio.CancelledError:raise
        except Exception as exc:
            if g==self.generation and (self.stop_event is None or not self.stop_event.is_set()):
                self.notice='Request failed: '+type(exc).__name__+'. No automatic retry.'
                self.pending={'id':uuid.uuid4().hex,'text':'I could not finish this request. I will not retry automatically. Any completed actions were not undone.'}
                self.state='awaiting_display'
            self.observe('gui_text_request_failed',reason=type(exc).__name__)

class Server(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,address,console,loop):
        if address[0]!='127.0.0.1':raise ValueError('loopback_required')
        super().__init__(address,Handler);self.console=console;self.loop=loop;self.token=secrets.token_urlsafe(32);self.cookie=secrets.token_urlsafe(32)
    def dispatch(self,coro):
        future=asyncio.run_coroutine_threadsafe(coro,self.loop)
        try:return future.result(timeout=15)
        except TimeoutError:
            future.cancel();raise

class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.0'
    def setup(self):
        super().setup();self.connection.settimeout(5)
    def log_message(self,*args):pass
    def host_ok(self):return self.headers.get('Host') in {f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}
    def session_ok(self):
        try:c=SimpleCookie(self.headers.get('Cookie',''));return secrets.compare_digest(c['me3_gui'].value,self.server.cookie)
        except (KeyError,ValueError):return False
    def reply(self,status,body,kind='application/json; charset=utf-8',cookie=False):
        if not isinstance(body,bytes):body=json.dumps(body).encode('utf-8')
        self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(body)))
        for k,v in {'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'strict-origin-when-cross-origin','Content-Security-Policy':"default-src 'self'; script-src 'self' https://www.youtube.com https://s.ytimg.com; style-src 'self'; connect-src 'self' https://www.youtube.com; frame-src https://www.youtube.com; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"}.items():self.send_header(k,v)
        if cookie:self.send_header('Set-Cookie',f'me3_gui={self.server.cookie}; HttpOnly; SameSite=Strict; Path=/')
        self.end_headers();self.wfile.write(body)
    def do_GET(self):
        if not self.host_ok():return self.reply(403,{'error':'host_rejected'})
        path=urlsplit(self.path).path
        if path in {'/','/app.js','/style.css','/youtube.js'}:
            name={'/':'index.html','/app.js':'app.js','/style.css':'style.css','/youtube.js':'youtube.js'}[path];body=(WEB/name).read_bytes()
            if path=='/':body=body.replace(b'__CSRF__',self.server.token.encode())
            return self.reply(200,body,{'/':'text/html; charset=utf-8','/app.js':'text/javascript; charset=utf-8','/style.css':'text/css; charset=utf-8','/youtube.js':'text/javascript; charset=utf-8'}[path],cookie=path=='/')
        if path!='/api/state':return self.reply(404,{'error':'not_found'})
        if not self.session_ok():return self.reply(403,{'error':'session_required'})
        # Source hashes/authorization and local store reads can block. Keep them
        # off the audio loop; snapshot is a read-only display projection. Every
        # start still validates fresh authorization before opening any resource.
        async def state():
            console=self.server.console
            return await console.read_state() if isinstance(console,Console) else await asyncio.to_thread(console.snapshot)
        try:self.reply(200,self.server.dispatch(state()))
        except Exception:self.reply(503,{'error':'state_unavailable'})
    def do_POST(self):
        try:
            size=int(self.headers.get('Content-Length','0'))
            if size<0:raise ValueError()
            body=self.rfile.read(min(size,32769))
        except (ValueError,TimeoutError):return self.reply(400,{'error':'invalid_request_body'})
        if self.path=='/api/youtube-bridge':
            token=getattr(self.server,'youtube_token',None)
            if (not token or not self.host_ok() or self.headers.get('Origin') is not None
                or not secrets.compare_digest(self.headers.get('Authorization',''),'Bearer '+token)):
                return self.reply(403,{'error':'request_rejected'})
            try:
                if not 0<size<=32768 or self.headers.get('Content-Type')!='application/json':raise ValueError('invalid_request_body')
                data=json.loads(body)
                if not isinstance(data,dict):raise ValueError('object_required')
                from me3_assistant.youtube_gui import bridge_command
                return self.reply(200,self.server.dispatch(bridge_command(self.server.console,data)))
            except Exception:return self.reply(400,{'error':'youtube_control_unavailable_or_unverified'})
        origin=self.headers.get('Origin');allowed={f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}'}
        if not self.host_ok() or not self.session_ok() or origin not in allowed or not secrets.compare_digest(self.headers.get('X-ME3-CSRF',''),self.server.token):return self.reply(403,{'error':'request_rejected'})
        if self.path!='/api/command':return self.reply(404,{'error':'not_found'})
        try:
            if not 0<size<=32768 or self.headers.get('Content-Type')!='application/json':raise ValueError('invalid_request_body')
            data=json.loads(body)
            if not isinstance(data,dict):raise ValueError('object_required')
            result=self.server.dispatch(self.server.console.command(data));self.reply(200,result)
        except ValueError as exc:self.reply(400,{'error':str(exc)})
        except Exception:self.reply(503,{'error':'command_failed_inspect_metadata'})

async def serve(config,port,reminder_path=None):
    from me3_assistant.reminders import Store
    # One project GUI owner; reminder life is independent of voice session Stop.
    path=Path(reminder_path or ROOT/'data/reminders.sqlite3').resolve()
    if not path.is_relative_to(ROOT.resolve()):raise ValueError('reminder_store_outside_project')
    with lock(path.with_suffix('.scheduler.lock')):
        store=Store(path);store.initialize()
        console=Console(config,verify_sources(),store);server=Server(('127.0.0.1',port),console,asyncio.get_running_loop())
        from me3_assistant.youtube_gui import install_bridge
        bridge_path=install_bridge(server,ROOT)
        async def tick():
            while True:
                try:store.tick()
                except Exception:console.notice='Reminder scheduler unavailable. Pending alerts are retained; inspect local storage.'
                await asyncio.sleep(1)
        ticker=asyncio.create_task(tick());thread=asyncio.create_task(asyncio.to_thread(server.serve_forever,poll_interval=0.2))
        print(json.dumps({'gui_url':f'http://127.0.0.1:{server.server_port}','source_revision':console.source['revision'],'standby':True,'audio_opened':False,'provider_calls':0}),flush=True)
        try:await asyncio.Event().wait()
        finally:
            await console.stop();ticker.cancel();await asyncio.gather(ticker,return_exceptions=True)
            store.shutdown();await asyncio.to_thread(server.shutdown);server.server_close();await thread

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--port',type=int,default=8765);p.add_argument('--config',type=Path,default=DEFAULT);p.add_argument('--reminder-store',type=Path);p.add_argument('--check',action='store_true');args=p.parse_args()
    from loguru import logger
    logger.remove();logging.disable(logging.CRITICAL)
    if sys.platform=='linux':
        # Explicit diagnostic signal prints thread stacks only (never locals,
        # transcripts or audio), even if the application loop is blocked.
        import faulthandler,signal
        faulthandler.register(signal.SIGUSR1,all_threads=True)
    config=load(args.config);source=verify_sources()
    if args.check:print(json.dumps({'source_revision':source['revision'],'standby_only':True,'provider_calls':0,'tts_calls':0}));return 0
    if not 1024<=args.port<=65535:raise ValueError('port_out_of_range')
    try:asyncio.run(serve(config,args.port,args.reminder_store))
    except KeyboardInterrupt:return 0
    return 0

if __name__=='__main__':raise SystemExit(main())
