"""Actual SQLite/MCP fixtures; no models, synthesis, native audio or production task data."""
import asyncio,json,sqlite3,tempfile,time,unittest,uuid,http.cookiejar,os,socket,subprocess,sys,urllib.request
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch
from task_mcp.client import ROOT
from me3_assistant.reminders import Store,instant,digest
from me3_assistant.registry import Registry,ToolFailure
from me3_assistant.actions import Actions,Journal
from me3_assistant.config import load
from me3_assistant.local_actions import LocalActionBudget,initialize_local_budget,validate_local_actions
from me3_assistant.gui import Console
from tests.test_assistant_mode import Events,FixtureBudget,Model,decision,history

class SpokenTimeTests(unittest.TestCase):
    def test_explicit_utc_time_avoids_iso_punctuation_without_changing_instant(self):
        from me3_assistant.reminder_action import spoken_time
        value='2026-10-22T09:04:48.172528+00:00'
        self.assertEqual(spoken_time(value),'22 October 2026 at 9:04 AM and 48 seconds UTC')
    def test_local_offsets_and_midnight_are_explicit(self):
        from me3_assistant.reminder_action import spoken_time
        self.assertEqual(spoken_time('2026-10-22T17:04:00+08:00'),'22 October 2026 at 5:04 PM UTC plus 8 hours')
        self.assertIn('12:00 AM UTC minus 5 hours 30 minutes',spoken_time('2026-10-22T00:00:00-05:30'))
        self.assertIn('UTC plus 0 hours 30 seconds',spoken_time('2026-10-22T00:00:00+00:00:30'))
    def test_naive_time_cannot_be_spoken_as_if_timezone_known(self):
        from me3_assistant.reminder_action import spoken_time
        with self.assertRaises(ValueError):spoken_time('2026-10-22T09:04:48')

class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.now=1780000000.;self.path=Path(self.tmp.name)/'reminders.sqlite3';self.store=Store(self.path,lambda:self.now);self.store.initialize();self.store.tick()
    def tearDown(self):self.tmp.cleanup()
    def schedule(self,seconds=90):
        op=uuid.uuid4().hex;i=self.store.prepare('schedule',kind='reminder',label='Check study notes',delay_seconds=seconds);self.store.commit(op,i);return op,i
    def test_missing_store_read_does_not_create(self):
        p=Path(self.tmp.name)/'missing';self.assertFalse(Store(p).listing()['available']);self.assertFalse(p.exists())
    def test_confirmation_commit_idempotent_and_identity_changes_rejected(self):
        op,i=self.schedule();self.assertTrue(self.store.commit(op,i)['duplicate'])
        with self.assertRaises(ValueError):self.store.commit(op,{**i,'label':'different'})
        self.assertEqual(len(self.store.listing()['reminders']),1)
    def test_due_once_survives_restart_and_acknowledgement(self):
        op,i=self.schedule();self.store.shutdown();self.now+=100
        self.assertFalse(self.store.listing()['scheduler_active']);restarted=Store(self.path,lambda:self.now);restarted.tick();a=restarted.listing()['reminders'][0];self.assertEqual(a['state'],'due');self.assertEqual(a['version'],1)
        restarted.tick();self.assertEqual(restarted.listing()['reminders'][0]['version'],1)
        self.assertTrue(restarted.acknowledge(op));self.assertFalse(restarted.acknowledge(op));restarted.tick();self.assertEqual(restarted.listing()['reminders'],[])
    def test_cancel_before_due_never_emits_alert_and_preserves_other_row(self):
        a,_=self.schedule();b,_=self.schedule();i=self.store.prepare('cancel',reminder_id=a);self.store.commit(uuid.uuid4().hex,i)
        self.now+=100;self.store.tick();self.assertEqual([r['id'] for r in self.store.listing()['reminders']],[b])
    def test_cancel_race_after_due_is_truthfully_rejected(self):
        a,_=self.schedule(10);i=self.store.prepare('cancel',reminder_id=a);self.now+=11;self.store.tick()
        with self.assertRaisesRegex(ValueError,'already_due'):self.store.commit(uuid.uuid4().hex,i)
        self.store.tick();self.assertEqual(self.store.listing()['reminders'][0]['state'],'due')
    def test_timezone_and_ambiguous_or_past_time(self):
        self.assertEqual(instant('2026-10-22T16:00:00+08:00'),instant('2026-10-22T08:00:00Z'))
        for value in ('2026-10-22T16:00:00','tomorrow morning','2020-01-01T12:00:00+08:00'):
            with self.subTest(value=value),self.assertRaises(ValueError):self.store.prepare('schedule',kind='alarm',label='Wake',due_at=value)
    def test_invalid_arguments_never_create_rows(self):
        for kwargs in ({'delay_seconds':True},{'delay_seconds':-1},{'delay_seconds':2592001},{'delay_seconds':90,'due_at':'2026-10-22T16:00:00+08:00'},{'delay_seconds':90,'label':'bad\nlabel'},{'delay_seconds':90,'kind':'recurring'}):
            args={'kind':'alarm','label':'Wake',**kwargs}
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):self.store.prepare('schedule',**args)
        self.assertEqual(self.store.listing()['reminders'],[])
    def test_scheduler_down_or_stale_fails_closed_without_schedule(self):
        i=self.store.prepare('schedule',kind='alarm',label='Wake',delay_seconds=90);self.now+=6
        with self.assertRaises(ValueError):self.store.commit(uuid.uuid4().hex,i)
        with self.assertRaises(ValueError):self.store.prepare('schedule',kind='alarm',label='Wake',delay_seconds=90)
        self.assertEqual(self.store.listing()['reminders'],[])
    def test_too_late_confirmation_cannot_silently_change_due_time(self):
        i=self.store.prepare('schedule',kind='alarm',label='Wake',delay_seconds=10);self.now+=8;self.store.tick()
        with self.assertRaisesRegex(ValueError,'time_passed'):self.store.commit(uuid.uuid4().hex,i)
    def test_live_wall_clock_short_due_transition_has_no_external_worker(self):
        # Real clock progression in an isolated DB, using an already-overdue
        # persisted timestamp to exercise actual polling without a 10s wait.
        self.schedule();wall=Store(self.path);wall.tick();self.assertEqual(wall.listing()['reminders'][0]['state'],'due')

class ReminderMCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_whole_json_float_canonicalized_without_weakening_strict_numbers(self):
        from me3_assistant.reminder_server import JsonInt
        from pydantic import TypeAdapter,ValidationError
        parser=TypeAdapter(JsonInt);self.assertEqual(parser.validate_python(90.0),90)
        for value in (True,'90',90.5,float('nan'),float('inf')):
            with self.subTest(value=value),self.assertRaises(ValidationError):parser.validate_python(value)
        async with Registry(self.events,self.cfg) as r:
            a=self.actions(r);prompt=await a.propose('propose_reminder',{'command':'schedule','kind':'alarm','label':'Fixture','delay_seconds':90.0},1)
            self.assertIsNotNone(a.pending);self.assertIn('Schedule alarm',prompt);self.assertEqual(self.store.listing()['reminders'],[])
            page=await r.read('reminders__list_reminders',1,{'offset':0.0,'limit':8.0});self.assertEqual(page['total'],0)
    async def test_rejection_metadata_uses_only_whitelisted_codes_no_private_arguments(self):
        async with Registry(self.events,self.cfg) as r:
            self.ticker.cancel();await asyncio.gather(self.ticker,return_exceptions=True);self.store.shutdown()
            with self.assertRaises(ToolFailure):await r.rpc('reminders','prepare_reminder',1,{'command':'schedule','kind':'alarm','label':'PRIVATE FIXTURE LABEL','delay_seconds':90,'operation_id':uuid.uuid4().hex})
            errors=[x for x in self.events.rows if x['event']=='assistant_rpc_error'];self.assertEqual(errors[-1]['validation_reason'],'reminder_scheduler_not_running');self.assertNotIn('PRIVATE FIXTURE',json.dumps(self.events.rows))
    async def asyncSetUp(self):
        (ROOT/'.tmp').mkdir(exist_ok=True);self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='reminder-mcp-');self.root=Path(self.tmp.name);self.store=Store(self.root/'reminders.sqlite3');self.store.initialize();self.store.tick();self.events=Events(self.root/'events.jsonl')
        cfg={'version':1,'runtime':{'max_session_seconds':600,'max_turns':8,'tool_rounds':3,'batch_calls':3,'rpc_calls':100},'servers':[{'id':'reminders','module':'me3_assistant.reminder_server','args':['--store',str(self.store.path)],'identity':'me3','role':'utility','read_tools':['list_reminders']}],'actions':[{'name':'propose_reminder','server':'reminders','adapter':'reminder_control'}]}
        p=self.root/'config.json';p.write_text(json.dumps(cfg));self.cfg=load(p)
        async def heartbeat():
            while True:self.store.tick();await asyncio.sleep(.5)
        self.ticker=asyncio.create_task(heartbeat())
    async def asyncTearDown(self):self.ticker.cancel();await asyncio.gather(self.ticker,return_exceptions=True);self.tmp.cleanup()
    def actions(self,r,n=3):
        g={'allowed_actions':['propose_reminder'],'local_action_limits':{'reminder_control':n},'expires_at_utc':(datetime.now(timezone.utc)+timedelta(minutes=10)).isoformat()}
        r.session_grant=g;r.local_action_directory=self.root/'budgets';initialize_local_budget(g,r.local_action_directory);r.budget=FixtureBudget(0);r.journal=Journal(self.root/'journal')
        return Actions(r,self.cfg['actions'],r.budget,r.journal,self.events,lambda g:True,allowed=['propose_reminder'])
    async def test_real_mcp_discovery_proposal_confirmation_verification_and_cancel(self):
        async with Registry(self.events,self.cfg) as r:
            a=self.actions(r);self.assertIn('delay_seconds',a.declarations()[0]['parameters']['properties']);self.assertNotIn('operation_id',a.declarations()[0]['parameters']['properties'])
            prompt=await a.propose('propose_reminder',{'command':'schedule','kind':'reminder','label':'Check notes','delay_seconds':90},1);op=a.pending['operation_id']
            self.assertEqual(self.store.listing()['reminders'],[])
            self.assertEqual(await a.confirm(history('Confirm?',prompt),2,clarify_only=True),prompt)
            self.assertEqual(await a.confirm(history('Confirm',prompt,'cancelled'),2),prompt)
            reply=await a.confirm(history('Confirm',prompt),2);self.assertIn('independently checked',reply);self.assertEqual(a.budget._used('reminder_control'),1);self.assertEqual(r.budget.used,0)
            self.assertIn('no pending',await a.confirm(history('Confirm',reply),3));self.assertEqual(len(self.store.listing()['reminders']),1)
            prompt=await a.propose('propose_reminder',{'command':'cancel','reminder_id':op},4);self.assertIn('Canceled',await a.confirm(history('Confirm',prompt),5));self.assertEqual(self.store.listing()['reminders'],[])
            self.assertTrue(await a.status(6));self.assertEqual(a.budget._used('reminder_control'),2)
    async def test_real_mcp_bad_token_or_unheard_or_superseded_never_schedules(self):
        async with Registry(self.events,self.cfg) as r:
            a=self.actions(r);prompt=await a.propose('propose_reminder',{'command':'schedule','kind':'alarm','label':'Wake','delay_seconds':90},1);op=a.pending['operation_id']
            with self.assertRaises(ToolFailure):await r.rpc('reminders','commit_reminder',2,{'operation_id':op,'token':'wrong','confirmation':'confirm'})
            a.current=lambda g:False;self.assertEqual(await a.confirm(history('Confirm',prompt),2),'');a.cancel();self.assertEqual(self.store.listing()['reminders'],[]);self.assertEqual(a.budget._used('reminder_control'),0)
    async def test_effect_endpoints_cannot_be_exposed_as_model_reads(self):
        self.cfg['servers'][0]['read_tools']=['commit_reminder']
        with self.assertRaisesRegex(ToolFailure,'cannot_be_read_tool'):
            async with Registry(self.events,self.cfg):pass
    async def test_generic_model_selected_tool_and_controller_confirm(self):
        from me3_assistant.router import Router
        from types import SimpleNamespace
        async with Registry(self.events,self.cfg) as r:
            a=self.actions(r);r.allowed_actions=['propose_reminder'];router=Router(Model(decision('propose_reminder',{'command':'schedule','kind':'reminder','label':'Check notes','delay_seconds':90})),r,self.events)
            router.actions=a;router.controller=SimpleNamespace(clock=SimpleNamespace(current=1),current=lambda g:True)
            prompt=await router.answer(history('Remind me to check notes in ninety seconds.'));self.assertIn('Check notes',prompt)
            router.ordinary=Model();reply=await router.answer(history('Confirmed.',prompt));self.assertIn('independently checked',reply);self.assertEqual(router.ordinary.seen,[])
    async def test_full_catalog_pages_stay_inside_mcp_response_bound(self):
        for _ in range(32):self.store.tick();self.store.commit(uuid.uuid4().hex,self.store.prepare('schedule',kind='reminder',label='x'*120,delay_seconds=90))
        async with Registry(self.events,self.cfg) as r:
            first=await r.read('reminders__list_reminders',1,{'limit':16});second=await r.read('reminders__list_reminders',1,{'limit':16,'offset':first['next_offset']})
            self.assertEqual(first['total'],32);self.assertEqual(len(first['reminders'])+len(second['reminders']),32);self.assertIsNone(second['next_offset']);self.assertLess(len(json.dumps(first)),12000)

class LocalAllowanceTests(unittest.TestCase):
    def test_music_legacy_ledger_unchanged_and_independent_new_reminder_counter(self):
        with tempfile.TemporaryDirectory() as d:
            g={'allowed_actions':['a','b'],'local_action_limits':{'music_control':1,'reminder_control':2},'expires_at_utc':(datetime.now(timezone.utc)+timedelta(minutes=10)).isoformat()};cfg={'actions':[{'name':'a','adapter':'music_control'},{'name':'b','adapter':'reminder_control'}]};validate_local_actions(g,cfg)
            p=initialize_local_budget(g,d);base=FixtureBudget(0);a=LocalActionBudget(base,g,d);a.reserve('reminder_control');a.reserve('music_control');b=LocalActionBudget(base,g,d);self.assertEqual(b.reserve('reminder_control'),2);self.assertFalse(a.permitted('reminder_control'));self.assertFalse(b.permitted('music_control'));self.assertEqual(base.used,0)
            data=json.loads(p.read_text());data['reminder_control']=0;p.write_text(json.dumps(data))
            with self.assertRaises(ValueError):b.permitted('music_control')
    def test_no_reminder_cap_means_no_effect_permission(self):
        with tempfile.TemporaryDirectory() as d:self.assertFalse(LocalActionBudget(FixtureBudget(0),{},d).permitted('reminder_control'))

class GuiReminderTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_failure_is_recorded_and_same_grant_cannot_be_retried(self):
        import hashlib
        from unittest.mock import AsyncMock
        grant={'nonce':1};binding=hashlib.sha256(json.dumps(grant,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        with tempfile.TemporaryDirectory() as d,patch('me3_assistant.gui.NORMAL',Path(d)),patch('me3_assistant.runner.run',AsyncMock(side_effect=ValueError('Project ALSA route me3_speaker is unavailable; inspect config/asound-pi.conf'))):
            c=Console({}, {'revision':'fixture'});c.voice_grant_binding=binding;await c.voice();self.assertEqual(c.failed_voice_grant,binding);self.assertTrue((Path(d)/'gui-failures.jsonl').exists())
            with patch('me3_assistant.gui.verify_sources',return_value={'revision':'fixture'}),patch('me3_assistant.gui.authorize',return_value=(grant,{})),patch('me3_assistant.gui.sys.platform','linux'):
                with self.assertRaisesRegex(ValueError,'fresh_authorization'):c.gate('normal')
    async def test_reminder_fault_cannot_remove_voice_stop_controls(self):
        from types import SimpleNamespace
        def broken():raise sqlite3.OperationalError('fixture only')
        c=Console({'sha256':'fixture'},{'revision':'fixture'},SimpleNamespace(listing=broken));c.mode='voice';c.state='listening';c.task=asyncio.create_task(asyncio.sleep(10))
        with patch.object(c,'gate',side_effect=ValueError('no_allowance')):
            state=c.snapshot();self.assertTrue(state['active']);self.assertFalse(state['reminders']['available'])
        c.task.cancel();await asyncio.gather(c.task,return_exceptions=True)
    async def test_voice_stop_does_not_cancel_persistent_due_alert_and_dismiss_is_targeted(self):
        with tempfile.TemporaryDirectory() as d:
            now=[1780000000.];s=Store(Path(d)/'db',lambda:now[0]);s.initialize();s.tick();op=uuid.uuid4().hex;s.commit(op,s.prepare('schedule',kind='alarm',label='Test',delay_seconds=10))
            c=Console({}, {'revision':'fixture'},s);await c.stop();now[0]+=11;s.tick();self.assertEqual(s.listing()['reminders'][0]['state'],'due')
            with self.assertRaises(ValueError):await c.command({'action':'ack_reminder','reminder_id':'../wrong'})
            await c.command({'action':'ack_reminder','reminder_id':op});self.assertEqual(s.listing()['reminders'],[])

class RealGuiSchedulerTests(unittest.TestCase):
    def test_isolated_gui_restart_delivers_overdue_once_without_voice_session(self):
        (ROOT/'.tmp').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='reminder-gui-') as d:
            path=Path(d)/'db';s=Store(path,lambda:time.time()-60);s.initialize();s.tick();op=uuid.uuid4().hex;s.commit(op,s.prepare('schedule',kind='reminder',label='Fixture only',delay_seconds=10));s.shutdown()
            def start():
                with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
                log=open(Path(d)/('gui-'+str(port)+'.log'),'xb');proc=subprocess.Popen([sys.executable,'-B','-m','me3_assistant.gui','--port',str(port),'--reminder-store',str(path)],cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=log,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
                opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()));end=time.monotonic()+20
                try:
                    while True:
                        try:
                            opener.open(f'http://127.0.0.1:{port}/',timeout=1).close();data=json.load(opener.open(f'http://127.0.0.1:{port}/api/state',timeout=2))
                            if data['reminders']['scheduler_active']:return proc,log,data
                        except (OSError,ValueError):pass
                        if proc.poll() is not None or time.monotonic()>end:raise AssertionError('isolated_gui_failed_to_start')
                        time.sleep(.1)
                except BaseException:proc.terminate();proc.wait(timeout=5);log.close();raise
            for _ in range(2):
                proc,log,data=start()
                try:
                    self.assertFalse(data['active']);self.assertEqual(data['reminders']['reminders'][0]['id'],op);self.assertEqual(data['reminders']['reminders'][0]['state'],'due');self.assertEqual(data['reminders']['reminders'][0]['version'],1)
                finally:proc.terminate();proc.wait(timeout=8);log.close()
