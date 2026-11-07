import asyncio,contextlib,hashlib,io,json,os,sqlite3,tempfile,unittest
from decimal import Decimal
from datetime import datetime,timedelta,timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,AsyncMock
from google.genai import types
from task_mcp.client import ROOT
from me3_assistant.config import load
from me3_assistant.registry import Registry,ToolFailure
from me3_assistant.actions import Journal,Actions
from me3_assistant.router import Router
from me3_assistant.guard import authorize,SharedBudget,lock

class Events:
    def __init__(self,path):self.path=path;self.rows=[]
    def emit(self,event,**kw):self.rows.append({'event':event,**kw})

class FixtureBudget:
    def __init__(self,n=1):self.remaining=n;self.used=0
    def permitted(self,key):return self.remaining>0
    def reserve(self,key):
        if not self.permitted(key):raise ValueError('fixture_budget_exhausted')
        self.remaining-=1;self.used+=1

def config(kind='utility',database=None):
    spec={'id':'configured','module':'me3_assistant.utility_server','args':[],'identity':'me3','role':'utility','read_tools':['calculate_expression','get_current_time']}
    actions=[]
    if kind=='extension':spec.update(module='tests.assistant_fixture_server',identity='plain',read_tools=['count_words','delayed_label'])
    if kind=='shopping':
        spec.update(module='task_mcp.server',identity='me3',role='task',args=['--test-db',str(database)],read_tools=['list_items'])
        actions=[{'name':'propose_shopping_item','server':'configured','adapter':'shopping_item'}]
    return {'version':1,'runtime':{'max_session_seconds':600,'max_turns':8,'tool_rounds':3,'batch_calls':3,'rpc_calls':32},'servers':[spec],'actions':actions}

def history(text,prompt=None,control=None):
    old=[{'type':'model_output','content':[{'type':'text','text':prompt}],**({'control_state':control} if control else {})}] if prompt else []
    return old+[{'type':'user_input','content':[{'type':'text','text':text}]}]

def decision(name=None,args=None,text='Hello.'):
    call=types.FunctionCall(name=name,args=args or {}) if name else None
    content=types.Content(role='model',parts=[types.Part(function_call=call)] if call else [types.Part(text=text)])
    return content,[call] if call else [],'' if call else text

class Model:
    def __init__(self,*answers):self.answers=iter(answers);self.seen=[]
    async def decide(self,h,d,c):self.seen.append((h,d,list(c)));return next(self.answers)

class ConfigTests(unittest.TestCase):
    def setUp(self):
        (ROOT/'.tmp').mkdir(exist_ok=True);self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='normal-config-');self.folder=Path(self.tmp.name);self.path=self.folder/'config.json'
    def tearDown(self):self.tmp.cleanup()
    def test_default_is_project_bounded_and_caps_configured(self):
        self.path.write_text(json.dumps(config()));cfg=load(self.path)
        self.assertEqual(cfg['runtime']['max_turns'],8);self.assertEqual(len(cfg['approved_modules']),1)
    def test_shell_path_escape_bad_bounds_unknown_adapter_and_duplicate_alias_rejected(self):
        variants=[]
        for change in ['shell','args','bounds','adapter','duplicate']:
            c=config()
            if change=='shell':c['servers'][0]['module']='os;echo'
            elif change=='args':c['servers'][0]['args']=['\x00']
            elif change=='bounds':c['runtime']['max_turns']=True
            elif change=='adapter':c['actions']=[{'name':'do_it','server':'configured','adapter':'shell'}]
            else:c['servers'].append(c['servers'][0].copy())
            self.path.write_text(json.dumps(c))
            with self.subTest(change=change),self.assertRaises(ValueError):load(self.path)
        with self.assertRaises(ValueError):load(Path(self.tmp.name).parent.parent.parent/'outside.json')
    def grant(self):
        cfg=config();cfg['sha256']='config';cfg['approved_modules']=[]
        limits={'asr':72,'reasoning':64,'tts':102,'commit_item':1};used={'asr':71,'reasoning':62,'tts':98,'commit_item':1}
        authority={'approved':True,'limits':limits};(self.folder/'authorization.json').write_text(json.dumps(authority));(self.folder/'budget.json').write_text(json.dumps(used))
        now=datetime.now(timezone.utc)
        grant={'approved':True,'mode':'normal','provider_allowance_confirmed':True,'operator_ready':True,'source_revision':'normal','config_sha256':'config','budget_authorization_sha256':hashlib.sha256((self.folder/'authorization.json').read_bytes()).hexdigest(),'runtime':cfg['runtime'],'issued_at_utc':now.isoformat(),'expires_at_utc':(now+timedelta(minutes=10)).isoformat(),'budget_start':used,'max_reservations':{'asr':1,'reasoning':2,'tts':4,'commit_item':0},'allowed_actions':[]}
        return cfg,{'revision':'normal','budget_limits':limits,'source_hashes':{}},grant,now
    def test_fresh_bound_authorization_and_exhausted_writes(self):
        cfg,src,g,now=self.grant();p=self.folder/'normal.json';p.write_text(json.dumps(g))
        approved,limits=authorize(cfg,src,p,self.folder,now);b=SharedBudget(approved,limits,self.folder)
        self.assertFalse(b.permitted('commit_item'))
        self.assertEqual(b.reserve('asr'),72)
        with self.assertRaises(Exception):b.reserve('asr')
        self.assertEqual(json.loads((self.folder/'budget.json').read_text())['asr'],72)
    def test_stale_readiness_binding_and_insufficient_startup_rejected(self):
        for variant in ['old','changed','tts','tts_three','actions','module']:
            cfg,src,g,now=self.grant();p=self.folder/'normal.json'
            if variant=='old':g['issued_at_utc']=(now-timedelta(hours=1)).isoformat()
            elif variant=='changed':g['source_revision']='other'
            elif variant=='tts':g['max_reservations']['tts']=2
            elif variant=='tts_three':g['max_reservations']['tts']=3
            elif variant=='actions':g['allowed_actions']=['unapproved_action']
            else:cfg['approved_modules']=[{'module':'unlisted_module','sha256':'x'}]
            p.write_text(json.dumps(g))
            with self.subTest(variant=variant),self.assertRaises(ValueError):authorize(cfg,src,p,self.folder,now)
    def test_second_normal_session_cannot_take_lease(self):
        with lock(self.folder/'lease'):
            with self.assertRaises(ValueError):
                with lock(self.folder/'lease'):pass
    def test_cli_without_grant_never_opens_audio_or_model(self):
        from me3_assistant.runner import main
        cfg,src,g,now=self.grant()
        with patch('sys.argv',['normal']),patch('me3_assistant.runner.load',return_value=cfg),patch('me3_assistant.runner.verify_sources',return_value=src),patch('me3_assistant.runner.authorize',side_effect=ValueError('normal_live_authorization_required')),patch('me3_assistant.runner.run') as run,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(),2);run.assert_not_called()
    def test_mid_session_counter_decrease_or_authority_change_fails_closed(self):
        cfg,src,g,now=self.grant();b=SharedBudget(g,src['budget_limits'],self.folder)
        data=json.loads((self.folder/'budget.json').read_text());data['asr']=0;(self.folder/'budget.json').write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError,'decreased'):b.reserve('asr')
        (self.folder/'budget.json').write_text(json.dumps(g['budget_start']));(self.folder/'authorization.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'authority_changed'):b.reserve('tts')
    def test_corrupt_action_journal_fails_closed(self):
        p=self.folder/'journal.jsonl';p.write_text('{"operation_id":"not-an-operation","state":"VERIFIED"}\n')
        with self.assertRaises(ValueError):Journal(p)

class MCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_normal_observer_preserves_callback_audio_and_records_first_nonzero_once(self):
        from me3_assistant.runner import observe_pipeline
        events=Events(None)
        processor=SimpleNamespace(vad=SimpleNamespace(analyze_audio=AsyncMock(return_value=SimpleNamespace(name='QUIET'))),controller=SimpleNamespace(clock=SimpleNamespace(current=1)))
        state=SimpleNamespace(last_callback_epoch=1,capture_count=9,post=events.emit)
        output=SimpleNamespace(callback=lambda *a:(b'\x01\x00',0))
        transport=SimpleNamespace(state=state,output=lambda:output)
        settings=SimpleNamespace(input_device=2,output_device=3,output_rate=48000,output_channels=2)
        observe_pipeline((None,None,transport,processor),settings,events)
        self.assertEqual((await processor.vad.analyze_audio(bytes(640))).name,'QUIET')
        self.assertEqual(output.callback(),(b'\x01\x00',0));self.assertEqual(output.callback(),(b'\x01\x00',0))
        self.assertEqual(sum(x['event']=='core_first_submission' for x in events.rows),1)
        device=next(x for x in events.rows if x['event']=='assistant_audio_configuration')
        self.assertEqual((device['input_device_index'],device['output_device_index']),(2,3))
    async def asyncSetUp(self):
        (ROOT/'.tmp').mkdir(exist_ok=True);self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='normal-mcp-');self.folder=Path(self.tmp.name);self.events=Events(self.folder/'events.jsonl')
    async def asyncTearDown(self):self.tmp.cleanup()
    def cfg(self,kind='utility'):
        p=self.folder/'config.json';p.write_text(json.dumps(config(kind,self.folder/'test.sqlite3')));return load(p)
    async def test_actual_discovery_calculation_clock_and_shutdown(self):
        async with Registry(self.events,self.cfg()) as r:
            self.assertEqual({d['name'] for d in r.declarations()},{'configured__calculate_expression','configured__get_current_time'})
            self.assertEqual(Decimal((await r.read('configured__calculate_expression',1,{'expression':'250*.18/3'}))['result']),Decimal('15'))
            self.assertTrue((await r.read('configured__get_current_time',1,{'utc_offset_minutes':480}))['iso_time'].endswith('+08:00'))
    async def test_new_configured_tool_without_router_changes_and_no_provider_credentials(self):
        with patch.dict(os.environ,{'GOOGLE_API_KEY':'private-fixture-never-forward'}):
            async with Registry(self.events,self.cfg('extension')) as r:
                self.assertIn('configured__count_words',{d['name'] for d in r.declarations()})
                result=await r.read('configured__count_words',1,{'text':'a varied user request'})
                self.assertEqual(result['words'],4);self.assertFalse(result['provider_key_present'])
                with self.assertRaises(ToolFailure):await r.read('configured__hidden_action',1,{})
                self.assertNotIn('private-fixture',json.dumps(self.events.rows))
    async def test_whole_schema_rejects_identity_injection_and_invalid_boolean(self):
        async with Registry(self.events,self.cfg()) as r:
            for args in [{'expression':'2+2','generation':100},{'expression':'2+2','unexpected':1}]:
                with self.assertRaises(Exception):await r.read('configured__calculate_expression',1,args)
            with self.assertRaises(Exception):await r.read('configured__get_current_time',1,{'utc_offset_minutes':True})
            self.assertEqual(r.calls,0)
    async def test_actual_rpc_cancel_then_fresh_call_on_same_connection(self):
        async with Registry(self.events,self.cfg('extension')) as r:
            work=asyncio.create_task(r.read('configured__delayed_label',1,{'label':'fixture'}));await asyncio.sleep(.05);work.cancel()
            with self.assertRaises(asyncio.CancelledError):await work
            self.assertEqual((await r.read('configured__count_words',2,{'text':'after cancel'}))['words'],2)
    async def test_model_tool_roundtrip_open_answer_and_cached_duplicate_rpc(self):
        async with Registry(self.events,self.cfg('extension')) as r:
            r.budget=FixtureBudget(0);r.journal=Journal(self.folder/'journal.jsonl');r.allowed_actions=[]
            m=Model(decision('configured__count_words',{'text':'one two'}),decision('configured__count_words',{'text':'one two'}),decision(text='Two words.'))
            router=Router(m,r,self.events)
            self.assertEqual(await router.answer(history('How many words?')),'Two words.');self.assertEqual(r.calls,1)
            self.assertEqual(m.seen[1][2][-1].parts[0].function_response.response['result']['words'],2)
            router.ordinary=Model(decision(text='A clear explanation.'))
            self.assertEqual(await router.answer(history('Explain a concept.')),'A clear explanation.');self.assertEqual(r.calls,1)
    async def test_invalid_batch_runs_nothing_then_tool_failure_recovers(self):
        async with Registry(self.events,self.cfg()) as r:
            r.budget=FixtureBudget(0);r.journal=Journal(self.folder/'journal.jsonl');r.allowed_actions=[]
            router=Router(Model(decision('commit_item',{}),decision('configured__calculate_expression',{'expression':'1/0'}),decision(text='Recovered.')),r,self.events)
            self.assertIn('safely',await router.answer(history('do an unknown action')));self.assertEqual(r.calls,0)
            self.assertIn('could not finish',await router.answer(history('divide by zero')))
            self.assertEqual(await router.answer(history('hello')),'Recovered.')
    async def test_invalid_second_call_prevents_valid_first_call_in_same_batch(self):
        async with Registry(self.events,self.cfg()) as r:
            r.budget=FixtureBudget(0);r.journal=Journal(self.folder/'journal.jsonl');r.allowed_actions=[]
            calls=[types.FunctionCall(name='configured__calculate_expression',args={'expression':'2+2'}),types.FunctionCall(name='commit_item',args={})]
            content=types.Content(role='model',parts=[types.Part(function_call=x) for x in calls])
            router=Router(Model((content,calls,'')),r,self.events)
            self.assertIn('safely',await router.answer(history('two operations')));self.assertEqual(r.calls,0)
    async def test_direct_normal_runner_rechecks_authorization_before_opening_audio(self):
        from me3_assistant.runner import run
        with patch('me3_assistant.runner.load',return_value={'path':'fixture'}),patch('me3_assistant.runner.verify_sources',return_value={}),patch('me3_assistant.runner.authorize',side_effect=ValueError('normal_live_authorization_required')),patch('me3_assistant.runner.Settings.load') as settings:
            with self.assertRaisesRegex(ValueError,'normal_live_authorization'):await run({'path':'fixture'})
            settings.assert_not_called()
    async def test_tool_json_schema_reaches_existing_google_SDK_without_schema_loss(self):
        from me3_assistant.reasoning import Reasoning
        model=Reasoning.__new__(Reasoning);model.lock=asyncio.Lock();model.events=SimpleNamespace(current_generation=1,emit=lambda *a,**k:None)
        call=AsyncMock(return_value=SimpleNamespace(candidates=[SimpleNamespace(content=types.Content(role='model',parts=[types.Part(text='Hello.')]))]))
        model.traced=SimpleNamespace(provider=SimpleNamespace(client=SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=call)))))
        schema={'type':'object','properties':{'x':{'$ref':'#/$defs/value'}},'$defs':{'value':{'type':'integer','minimum':1}},'required':['x'],'additionalProperties':False}
        out=await model.decide(history('hello'),[{'name':'fixture','description':'Schema fixture','parameters':schema}],[])
        self.assertEqual(out[2],'Hello.');cfg=call.call_args.kwargs['config']
        self.assertEqual(cfg.tools[0].function_declarations[0].parameters_json_schema,schema)
        self.assertEqual(cfg.thinking_config.thinking_level,'LOW');self.assertTrue(cfg.automatic_function_calling.disable)
    async def test_normal_turn_bound_emits_stop_signal_without_extra_speech(self):
        from me3_assistant.runner import Events as NormalEvents
        events=NormalEvents(self.folder/'bounded')
        try:
            events.emit('turn_budget_reached',max_turns=8)
            self.assertTrue(events.bound_reached.is_set())
            self.assertNotIn('mcp_request_start',events.counts)
        finally:events.close()
    async def test_second_registered_action_adapter_needs_no_router_phrase_branch(self):
        from me3_assistant.actions import ADAPTERS,digest
        values={}
        class Adapter:
            budget_role='commit_item'
            def __init__(self,registry,spec):self.spec=spec
            def declaration(self):return {'name':self.spec['name'],'description':'Isolated in-memory action fixture.','parameters':{'type':'object','properties':{'label':{'type':'string'}},'required':['label'],'additionalProperties':False}}
            async def prepare(self,args,g,op):return {'label':args['label']},digest(args),'Store the fixture label'
            async def execute(self,p,g):values[p['operation_id']]=p['expected_digest']
            async def verify(self,p,g):return values.get(p['operation_id'])==p['expected_digest']
        cfg=config('extension');cfg['actions']=[{'name':'propose_label','server':'configured','adapter':'fixture'}]
        path=self.folder/'config.json';path.write_text(json.dumps(cfg))
        with patch.dict(ADAPTERS,{'fixture':Adapter}):
            async with Registry(self.events,load(path)) as r:
                r.budget=FixtureBudget(1);r.journal=Journal(self.folder/'journal.jsonl');r.allowed_actions=['propose_label']
                router=Router(Model(decision('propose_label',{'label':'fixture only'})),r,self.events)
                prompt=await router.answer(history('store the fixture label'))
                self.assertIn('confirm',prompt)
                self.assertIn('independently checked',await router.answer(history('confirm',prompt)))
                self.assertEqual(r.budget.used,1)
    @contextlib.asynccontextmanager
    async def action_router(self,enabled=True):
        async with Registry(self.events,self.cfg('shopping')) as r:
            r.budget=FixtureBudget(1 if enabled else 0);r.journal=Journal(self.folder/'journal.jsonl');r.allowed_actions=['propose_shopping_item'] if enabled else []
            yield Router(Model(decision('propose_shopping_item',{'item':'notebooks','quantity':3})),r,self.events),r
    async def test_real_MCP_non_demo_proposal_confirm_and_independent_SQL(self):
        async with self.action_router() as (router,r):
            prompt=await router.answer(history('Please save three notebooks.'))
            self.assertIn('3 notebooks',prompt);self.assertEqual(r.budget.used,0)
            reply=await router.answer(history('confirm',prompt));self.assertIn('independently checked',reply)
            with contextlib.closing(sqlite3.connect(self.folder/'test.sqlite3')) as db:self.assertEqual(db.execute('SELECT item,quantity FROM items').fetchall(),[('notebooks',3)])
            self.assertEqual(r.budget.used,1)
            self.assertIn('no pending',await router.answer(history('confirm',reply)))
            journal=(self.folder/'journal.jsonl').read_text();self.assertNotIn('notebooks',journal);self.assertNotIn('token',journal)
    async def test_incomplete_prompt_cancel_and_no_write_authorization(self):
        async with self.action_router() as (router,r):
            prompt=await router.answer(history('three notebooks'))
            self.assertEqual(await router.answer(history('confirm',prompt,'cancelled')),prompt);self.assertEqual(r.budget.used,0)
            self.assertIn('Canceled',await router.answer(history('cancel',prompt)));self.assertEqual(r.budget.used,0)
            self.assertEqual((await r.rpc('configured','list_items',1,{}))['rows'],[])
    async def test_no_allowed_action_never_prepares_or_commits(self):
        async with self.action_router(False) as (router,r):
            self.assertIn('safely',await router.answer(history('save notebooks')));self.assertEqual(r.calls,0)
            self.assertIn('not authorized',await router.actions.propose('propose_shopping_item',{'item':'notebooks','quantity':3},1));self.assertEqual(r.calls,0)
    async def test_expiry_and_stale_generation_do_not_dispatch(self):
        async with self.action_router() as (router,r):
            prompt=await router.answer(history('three notebooks'))
            router.actions.pending['expires']=0
            self.assertIn('expired',await router.answer(history('confirm',prompt)));self.assertEqual(r.budget.used,0)
            r.current=lambda g:False
            with self.assertRaises(ToolFailure):await r.read('configured__list_items',1,{})
    async def test_canceled_completed_write_is_uncertain_then_verified_without_recommit(self):
        async with self.action_router() as (router,r):
            prompt=await router.answer(history('three notebooks'))
            adapter=router.actions.adapters['propose_shopping_item'];verify=adapter.verify;entered=asyncio.Event()
            async def paused(record,g):entered.set();await asyncio.Event().wait()
            adapter.verify=paused
            work=asyncio.create_task(router.answer(history('confirm',prompt)));await asyncio.wait_for(entered.wait(),3);work.cancel()
            with self.assertRaises(asyncio.CancelledError):await work
            self.assertEqual(r.budget.used,1);self.assertEqual(len(r.journal.unresolved()),1)
            # Simulate process restart: recover only metadata/operation ID, never the commit token.
            r.journal=Journal(self.folder/'journal.jsonl');recovered=Router(Model(),r,self.events)
            status=await recovered.actions.status(2)
            self.assertEqual(status['status'],'independently_verified');self.assertFalse(status['write_retried']);self.assertEqual(r.budget.used,1)
            self.assertFalse(r.journal.unresolved())
    async def test_replacement_request_cancels_pending_proposal(self):
        async with self.action_router() as (router,r):
            prompt=await router.answer(history('three notebooks'))
            router.ordinary=Model(decision(text='The latest answer.'))
            self.assertEqual(await router.answer(history('Instead explain gravity.',prompt)),'The latest answer.')
            self.assertIsNone(router.pending);self.assertEqual(r.budget.used,0)
            self.assertIn('no pending',await router.answer(history('confirm','The latest answer.')))
    async def test_real_controller_uses_normal_router_and_completed_spoken_confirmation(self):
        import base64
        from me3_agent.controller import AgentController
        async with self.action_router() as (router,r):
            heard=[];inputs=iter(['Please save three notebooks.','Confirm.'])
            class FakeSpeech:
                async def call(self,role,g,**kw):return {'text':next(inputs)} if role=='asr' else {'pcm_b64':base64.b64encode(bytes(960)).decode()}
            async def play(pcm,g):heard.append(g)
            controller=AgentController(FakeSpeech(),router,self.events,play);router.controller=controller
            try:
                g=controller.interrupt();controller.submit(bytes(6400),g);await controller.task
                self.assertIn('3 notebooks',controller.history[-1]['content'][0]['text'])
                g=controller.interrupt();controller.submit(bytes(6400),g);await controller.task
                self.assertIn('independently checked',controller.history[-1]['content'][0]['text']);self.assertEqual(heard,[1,2]);self.assertEqual(r.budget.used,1)
            finally:await controller.close()
    async def test_real_controller_suppresses_superseded_tool_reply(self):
        import base64
        from me3_agent.controller import AgentController
        async with Registry(self.events,self.cfg('extension')) as r:
            r.budget=FixtureBudget(0);r.journal=Journal(self.folder/'journal.jsonl');r.allowed_actions=[]
            router=Router(Model(decision('configured__delayed_label',{'label':'obsolete'}),decision(text='Latest answer.')),r,self.events)
            class FakeSpeech:
                async def call(self,role,g,**kw):return {'text':'Hello.'} if role=='asr' else {'pcm_b64':base64.b64encode(bytes(960)).decode()}
            heard=[]
            async def play(pcm,g):heard.append(g)
            c=AgentController(FakeSpeech(),router,self.events,play);router.controller=c
            try:
                g=c.interrupt();c.submit(bytes(6400),g);old=c.task
                for _ in range(100):
                    if any(e['event']=='assistant_rpc_start' for e in self.events.rows):break
                    await asyncio.sleep(.005)
                g=c.interrupt();c.submit(bytes(6400),g);await c.task;await asyncio.gather(old,return_exceptions=True)
                self.assertEqual(heard,[2]);self.assertEqual(c.history[-1]['content'][0]['text'],'Latest answer.')
                self.assertTrue(any(e['event']=='assistant_rpc_canceled' for e in self.events.rows))
            finally:await c.close()
