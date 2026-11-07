import asyncio,tempfile,unittest,base64,time
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace
from google.genai import types
from me3_agent.tools import calculate,current_time
from me3_agent.router import AgentRouter
from me3_agent.client import AgentTasks
from task_mcp.client import ROOT,TaskError
from voice_agent import EventLog
class UtilityTests(unittest.TestCase):
    def test_decimal_and_compound_calculation(self):
        self.assertEqual(calculate('0.1 + 0.2')['result'],'0.3');self.assertEqual(calculate('(250 * 18 / 100) + 5')['result'],'50')
    def test_no_executable_or_unbounded_expressions(self):
        for x in ['__import__("os").system("echo unsafe")','x','[1]*100','2**10000000','1/0','1e309','9'*300,'- '*40+'1','True','abs(-1)']:
            with self.subTest(x=x),self.assertRaises(ValueError):calculate(x)
    def test_time_offset(self):
        self.assertTrue(current_time(480)['iso_time'].endswith('+08:00'))
        for x in [True,841,-721,'480']:
            with self.assertRaises(ValueError):current_time(x)
class Events:
    def __init__(self):self.rows=[]
    def emit(self,event,**kw):self.rows.append({'event':event,**kw})
def history(text,previous=None):
    return ([{'type':'model_output','content':[{'type':'text','text':previous}]}] if previous else [])+[{'type':'user_input','content':[{'type':'text','text':text}]}]
def decision(name=None,args=None,text='Done.'):
    call=types.FunctionCall(name=name,args=args or {}) if name else None
    content=types.Content(role='model',parts=[types.Part(function_call=call)] if call else [types.Part(text=text)])
    return content,[call] if call else [],text if not call else ''
class FakeTasks:
    def __init__(self):self.calls=[]
    def declarations(self):return [
      {'name':'calculate_expression','parameters':{'type':'object','properties':{'expression':{'type':'string'}},'required':['expression']}},
      {'name':'propose_shopping_item','parameters':{'type':'object','properties':{'item':{'type':'string'},'quantity':{'type':'integer'}},'required':['item','quantity']}},
      {'name':'cancel_pending_action','parameters':{'type':'object','properties':{}}}]
    async def call(self,tool,g,**args):
        self.calls.append((tool,g,args))
        if tool=='prepare_item':return {**args,'token':'a'*32}
        if tool=='commit_item':raise AssertionError('unexpected_commit')
        return {'ok':True,**calculate(args['expression'])}
class Model:
    def __init__(self,*responses):self.responses=iter(responses);self.seen=[]
    async def decide(self,h,d,c):self.seen.append(list(c));return next(self.responses)
class RouterTests(unittest.IsolatedAsyncioTestCase):
    def router(self,*responses):
        model=Model(*responses);tasks=FakeTasks();events=Events();r=AgentRouter(model,tasks,events);return r,model,tasks,events
    async def test_open_conversation_uses_no_tool(self):
        r,m,t,e=self.router(decision(text='Plants use sunlight.'));self.assertEqual(await r.answer(history('How do plants grow?')),'Plants use sunlight.');self.assertEqual(t.calls,[])
    async def test_model_selects_actual_tool_and_uses_result(self):
        r,m,t,e=self.router(decision('calculate_expression',{'expression':'250*18/100'}),decision(text='Eighteen percent is forty five.'))
        self.assertIn('forty five',await r.answer(history('work out eighteen percent of 250')));self.assertEqual(t.calls[0][0],'calculate_expression');self.assertEqual(m.seen[1][-1].parts[0].function_response.response['result'],'45')
    async def test_unknown_tool_and_commit_rejected(self):
        for name in ['commit_item','read_file','shell']:
            r,m,t,e=self.router(decision(name,{}));self.assertIn('safely',await r.answer(history('do it')));self.assertFalse(t.calls)
    async def test_identity_injection_rejected(self):
        r,m,t,e=self.router(decision('calculate_expression',{'expression':'2+2','generation':9}));self.assertIn('safely',await r.answer(history('four')));self.assertFalse(t.calls)
    async def test_bool_quantity_rejected(self):
        r,m,t,e=self.router(decision('propose_shopping_item',{'item':'rulers','quantity':True}));self.assertIn('safely',await r.answer(history('save rulers')));self.assertFalse(t.calls)
    async def test_natural_proposal_never_commits(self):
        r,m,t,e=self.router(decision('propose_shopping_item',{'item':'rulers','quantity':6}));reply=await r.answer(history('I need six rulers on that list'))
        self.assertIn('Say confirm',reply);self.assertEqual([x[0] for x in t.calls],['prepare_item']);self.assertEqual(r.pending['quantity'],6)
    async def test_model_output_cannot_confirm(self):
        r,m,t,e=self.router(decision(text='confirm'));self.assertEqual(await r.answer(history('what do you think?')),'confirm');self.assertFalse(t.calls)
    async def test_stale_decision_executes_nothing(self):
        r,m,t,e=self.router(decision('propose_shopping_item',{'item':'rulers','quantity':6}));r.current=lambda g:len(m.seen)==0
        self.assertEqual(await r.answer(history('save six rulers')),'');self.assertFalse(t.calls)
    async def test_tool_failure_recovers_next_question(self):
        r,m,t,e=self.router(decision('calculate_expression',{'expression':'1/0'}),decision(text='Hello.'))
        self.assertIn('could not',await r.answer(history('divide one by zero')));self.assertFalse(r.blocked);self.assertEqual(await r.answer(history('hello')),'Hello.')
    async def test_step_limit_is_bounded(self):
        r,m,t,e=self.router(*[decision('calculate_expression',{'expression':'2+2'})]*3);self.assertIn('limit',await r.answer(history('keep calculating')));self.assertEqual(len(t.calls),3)
    async def test_cancellation_propagates(self):
        r,m,t,e=self.router()
        async def canceled(*a):raise asyncio.CancelledError()
        m.decide=canceled
        with self.assertRaises(asyncio.CancelledError):await r.answer(history('hello'))
    async def test_new_question_abandons_pending_proposal(self):
        r,m,t,e=self.router(decision(text='The sky is blue.'));r.pending={'phase':'confirm'};await r.answer(history('why is the sky blue'));self.assertIsNone(r.pending)
class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_delayed_mcp_call_cancels_and_connection_recovers(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='agent-delay-') as folder:
            events=EventLog(Path(folder)/'logs')
            try:
                async with AgentTasks(events,test_db=Path(folder)/'test.sqlite3',utility_delay_ms=250) as client:
                    task=asyncio.create_task(client.call('calculate_expression',1,expression='2+2'));await asyncio.sleep(.05);task.cancel()
                    with self.assertRaises(asyncio.CancelledError):await task
                    self.assertEqual((await client.call('calculate_expression',2,expression='3+3'))['result'],'6');self.assertFalse(client.write_blocked)
            finally:events.close()
    def test_controlled_delay_is_explicit_and_bounded(self):
        from me3_agent.tools import server
        for delay in [-1,20001,True,'1']:
            with self.assertRaises(ValueError):server(ROOT/'.tmp/unused.sqlite3',delay)
    async def test_real_mcp_discovery_readonly_tasks_and_shutdown(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='agent-protocol-') as folder:
            events=EventLog(Path(folder)/'logs')
            try:
                async with AgentTasks(events,test_db=Path(folder)/'test.sqlite3') as client:
                    names={x['name'] for x in client.declarations()};self.assertNotIn('commit_item',names);self.assertIn('calculate_expression',names)
                    self.assertEqual((await client.call('calculate_expression',1,expression='18*250/100'))['result'],'45')
                    with self.assertRaises(TaskError):await client.call('calculate_expression',1,expression='1/0')
                    self.assertTrue((await client.call('get_current_time',2,utc_offset_minutes=480))['iso_time'].endswith('+08:00'))
                    self.assertEqual((await client.call('list_items',2))['rows'],[])
            finally:events.close()
class PrefixTests(unittest.IsolatedAsyncioTestCase):
    async def test_three_clips_survive_two_canceled_asr_requests(self):
        from me3_agent.controller import AgentController
        class Clients:
            def __init__(self):self.captured=[];self.started=asyncio.Event();self.block=True
            async def call(self,role,g,**kw):
                self.captured.append(base64.b64decode(kw['pcm_b64']));self.started.set()
                if self.block:await asyncio.Event().wait()
                return {'text':'Tell me the time and calculate the total.','generation':g,'request_id':'test'}
        clients=Clients();c=AgentController(clients,None,Events(),None);g=c.interrupt()
        clips=[bytes([n,0])*1600 for n in [1,2,3]]
        c.slot={'generation':g,'pcm':clips[1],'stage':'asr','submitted':time.monotonic(),'candidate':{'pcm':clips[0],'submitted':time.monotonic(),'generation':0}}
        task=asyncio.create_task(c.recognize(g,pcm_b64=base64.b64encode(clips[1]).decode(),sample_rate=16000));await clients.started.wait()
        self.assertEqual(clients.captured[0],clips[0]+clips[1]);task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        g=c.interrupt();saved=c.saved;c.saved=None
        self.assertEqual(saved['pcm'],clips[0]+clips[1]);self.assertFalse(saved.get('prefix_lost',False))
        c.slot={'generation':g,'pcm':clips[2],'stage':'asr','submitted':time.monotonic(),'candidate':saved};clients.block=False
        data=await c.recognize(g,pcm_b64=base64.b64encode(clips[2]).decode(),sample_rate=16000)
        self.assertEqual(clients.captured[-1],b''.join(clips));self.assertIn('calculate',data['text']);await c.close()
    async def test_overlong_combined_prefix_dispatches_no_asr(self):
        from me3_agent.controller import AgentController
        c=AgentController(None,None,Events(),None);g=c.interrupt();notices=[]
        async def clarify(g):notices.append(g);return {'text':''}
        c.clarify=clarify;c.slot={'generation':g,'pcm':bytes(240002),'stage':'asr','submitted':time.monotonic(),'candidate':{'pcm':bytes(240000),'submitted':time.monotonic(),'generation':0}}
        self.assertEqual(await c.recognize(g,pcm_b64='unused',sample_rate=16000),{'text':''});self.assertEqual(notices,[g]);await c.close()
class DeadlineTests(unittest.IsolatedAsyncioTestCase):
    async def test_known_audio_is_not_charged_to_processing_watchdog(self):
        from me3_agent.controller import AgentController
        class Clients:
            async def call(self,role,g,**kw):
                await asyncio.sleep(.3)
                return {'text':'Hello.','generation':g,'request_id':'asr-test'} if role=='asr' else {'pcm_b64':base64.b64encode(bytes(48000)).decode()}
        class Provider:
            async def answer(self,h):return 'Hello.'
        async def play(*a):await asyncio.sleep(.8)
        e=Events();c=AgentController(Clients(),Provider(),e,play,turn_deadline_seconds=1);g=c.interrupt();c.submit(bytes(6400),g);await c.task
        self.assertFalse(any(r['event']=='core_turn_deadline' for r in e.rows));self.assertTrue(any(r['event']=='response_complete' for r in e.rows));self.assertFalse(c.deadlines);await c.close()
    async def test_stuck_processing_remains_bounded_without_audio_credit(self):
        from me3_agent.controller import AgentController
        class Clients:
            async def call(self,role,g,**kw):
                if role=='asr':await asyncio.Event().wait()
                return {'pcm_b64':base64.b64encode(bytes(960)).decode()}
        async def play(*a):pass
        e=Events();c=AgentController(Clients(),SimpleNamespace(pending=None),e,play,turn_deadline_seconds=1);g=c.interrupt();c.submit(bytes(6400),g);await c.task
        self.assertEqual(sum(r['event']=='core_turn_deadline' for r in e.rows),1);self.assertFalse(any(r['event']=='agent_playback_deadline_credit' for r in e.rows));self.assertFalse(c.deadlines);await c.close()
class OverlapTests(unittest.IsolatedAsyncioTestCase):
    def setup_processor(self):
        from me3_agent.overlap import attach
        self.rows=[];self.old_task=object();self.g=3;self.calls=[];self.resumed=False
        e=SimpleNamespace(emit=lambda event,**kw:self.rows.append({'event':event,**kw}))
        async def takeover(reason):self.g+=1;self.calls.append(('takeover',reason));return self.g
        def resume():self.resumed=True
        def submit(pcm,g,data):self.calls.append(('submit',g,data['text']))
        c=SimpleNamespace(turns=0,max_turns=8,clock=SimpleNamespace(current=3),reserve_overlap_probe=lambda:True,credit_overlap=lambda g:self.calls.append(('credit',g)),listener_ack_count=0,submit_recognized=submit)
        self.p=SimpleNamespace(controller=c,valid=lambda token:True,takeover=takeover,pcm=bytearray(b'audio'),preroll=[],gate=SimpleNamespace(resume=resume),output_task=self.old_task,output_kind='response',output_generation=3,probing=True,state=SimpleNamespace(capture_count=1),transition=lambda *a:None)
        self.provider=SimpleNamespace(pending=None)
        async def call(role,g,**kw):return {'text':self.text,'generation':g,'request_id':'probe'}
        self.text='Okay';self.client=SimpleNamespace(call=call)
        attach(self.p,self.client,self.provider,e);return e
    async def test_ack_resumes_same_response_without_generation_change(self):
        self.setup_processor();await self.p.classify_overlap(1,b'audio',.6,3,self.old_task)
        self.assertTrue(self.resumed);self.assertEqual(self.g,3);self.assertEqual(self.p.controller.listener_ack_count,1);self.assertFalse(self.p.pcm)
    async def test_substantive_overlap_adopts_asr_without_duplicate_call(self):
        self.setup_processor();self.text='What is the capital of Japan?';await self.p.classify_overlap(1,b'audio',.8,3,self.old_task)
        self.assertFalse(self.resumed);self.assertEqual(self.calls[-1],('submit',4,self.text))
    async def test_pending_confirmation_cannot_be_treated_as_ack(self):
        self.setup_processor();self.provider.pending={'phase':'confirm'};await self.p.classify_overlap(1,b'audio',.6,3,self.old_task)
        self.assertFalse(self.resumed);self.assertEqual(self.calls[-1][0],'submit')
    async def test_late_result_cannot_resume_obsolete_speech(self):
        self.setup_processor()
        async def stale(*a,**kw):self.p.controller.clock.current=4;return {'text':'Okay'}
        self.client.call=stale;await self.p.classify_overlap(1,b'audio',.6,3,self.old_task)
        self.assertFalse(self.resumed);self.assertTrue(any(x['event']=='completion_overlap_result_stale' for x in self.rows))
    async def test_new_speech_cancels_probe_without_timeout_notice(self):
        self.setup_processor();started=asyncio.Event()
        async def blocked(*a,**kw):started.set();await asyncio.Event().wait()
        self.client.call=blocked;task=asyncio.create_task(self.p.classify_overlap(1,b'audio',.6,3,self.old_task));await started.wait();task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertFalse(self.resumed);self.assertFalse(any(x[0]=='takeover' for x in self.calls))
    async def test_watchdog_overlap_credit_is_bounded_once_per_response(self):
        from me3_agent.controller import AgentController
        c=AgentController(None,None,Events(),None)
        async with asyncio.timeout(100) as timer:
            before=timer.when();c.deadlines[1]={'timer':timer,'audio_credited':False};c.credit_overlap(1);c.credit_overlap(1);c.credit_overlap(2)
            self.assertAlmostEqual(timer.when()-before,35);c.deadlines.clear()
        await c.close()
class GatewayTests(unittest.IsolatedAsyncioTestCase):
    def session(self,**changes):
        from me3_agent.speech import GatewaySession
        self.invoked=0;self.data={'ok':False,'code':504,'error_type':'ServerError','cleanup_ok':True,'generation':2,'request_id':'asr-test','server_role':'asr',**changes}
        async def call(*args,**kwargs):self.invoked+=1;return SimpleNamespace(isError=False,structuredContent=self.data)
        self.owner=SimpleNamespace(gateway_timeouts=0,events=Events());return GatewaySession(SimpleNamespace(call_tool=call),self.owner)
    async def test_clean_504_recovers_without_retry_and_remains_bounded(self):
        from me3_m7.client import RecoverableASRTimeout
        session=self.session();args={'generation':2,'request_id':'asr-test'}
        for _ in range(2):
            with self.assertRaises(RecoverableASRTimeout):await session.call_tool('transcribe',args)
        result=await session.call_tool('transcribe',args)
        self.assertEqual(result.structuredContent['code'],504);self.assertEqual(self.invoked,3);self.assertEqual(self.owner.gateway_timeouts,2)
    async def test_unclean_other_error_and_wrong_identity_do_not_recover(self):
        for changes in [{'cleanup_ok':False},{'code':429},{'code':503},{'ok':True}]:
            session=self.session(**changes);await session.call_tool('transcribe',{'generation':2,'request_id':'asr-test'});self.assertEqual(self.owner.gateway_timeouts,0)
        session=self.session(request_id='obsolete')
        with self.assertRaisesRegex(ValueError,'identity'):await session.call_tool('transcribe',{'generation':2,'request_id':'asr-test'})
    async def test_fresh_asr_success_after_gateway_notice_in_same_controller(self):
        from me3_agent.controller import AgentController
        from me3_m7.client import RecoverableASRTimeout
        class Clients:
            def __init__(self):self.asr=0
            async def call(self,role,g,**kw):
                if role=='asr':
                    self.asr+=1
                    if self.asr==1:raise RecoverableASRTimeout('cleaned_provider_gateway_timeout')
                    return {'text':'Hello','generation':g,'request_id':'fresh'}
                return {'pcm_b64':base64.b64encode(bytes(960)).decode()}
        async def play(*args):pass
        async def answer(*args):return 'Hello.'
        e=Events();client=Clients();c=AgentController(client,SimpleNamespace(pending=None,answer=answer),e,play)
        for _ in range(2):g=c.interrupt();c.submit(bytes(6400),g);await c.task
        self.assertEqual(client.asr,2);self.assertTrue(any(r['event']=='m7_timeout_notice' for r in e.rows));self.assertTrue(any(r['event']=='response_complete' for r in e.rows));await c.close()
class ThinkingTests(unittest.TestCase):
    def test_profile_is_explicit_validated_and_default_unchanged(self):
        from me3_agent.reasoning import AgentReasoning
        with patch('me3_agent.reasoning.CoreReasoning.__init__',return_value=None) as core:
            self.assertEqual(AgentReasoning(Events()).thinking_profile,'low');self.assertEqual(AgentReasoning(Events(),'minimal').thinking_profile,'minimal');self.assertEqual(core.call_count,2)
            for invalid in ['auto','high',None]:
                with self.assertRaises(ValueError):AgentReasoning(Events(),invalid)
            self.assertEqual(core.call_count,2)
class SynthesisDeadlineTests(unittest.IsolatedAsyncioTestCase):
    async def test_stalled_synthesis_still_times_out_and_cleans_generation(self):
        from me3_agent.controller import AgentController
        class Clients:
            def __init__(self):self.syntheses=0
            async def call(self,role,g,**kw):
                if role=='asr':await asyncio.sleep(.9);return {'text':'Hello.','generation':g,'request_id':'asr-test'}
                self.syntheses+=1
                if self.syntheses==1:await asyncio.Event().wait()
                return {'pcm_b64':base64.b64encode(bytes(960)).decode()}
        async def answer(*args):return 'Hello.'
        async def play(*args):pass
        e=Events();c=AgentController(Clients(),SimpleNamespace(answer=answer,pending=None),e,play,turn_deadline_seconds=1);c.synthesis_budget_seconds=.3;g=c.interrupt();c.submit(bytes(6400),g);await c.task
        self.assertEqual(sum(r['event']=='core_turn_deadline' for r in e.rows),1);self.assertFalse(c.deadlines);await c.close()
    async def test_valid_synthesis_gets_own_budget_after_slow_asr(self):
        from me3_agent.controller import AgentController
        class Clients:
            async def call(self,role,g,**kw):
                await asyncio.sleep(.6)
                return {'text':'Hello.','generation':g,'request_id':'asr-test'} if role=='asr' else {'pcm_b64':base64.b64encode(bytes(960)).decode()}
        async def answer(*args):return 'Hello.'
        async def play(*args):pass
        e=Events();c=AgentController(Clients(),SimpleNamespace(answer=answer),e,play,turn_deadline_seconds=1);g=c.interrupt();c.submit(bytes(6400),g);await c.task
        self.assertFalse(any(r['event']=='core_turn_deadline' for r in e.rows));self.assertTrue(any(r['event']=='agent_synthesis_deadline_credit' for r in e.rows));self.assertFalse(c.deadlines);await c.close()
    async def test_synthesis_credit_cannot_extend_repeatedly_or_revive_old_generation(self):
        from me3_agent.controller import AgentController
        c=AgentController(None,None,Events(),None);c.synthesis_budget_seconds=2
        async with asyncio.timeout(1) as timer:
            c.deadlines[1]={'timer':timer,'audio_credited':False};c.credit_synthesis(1);after=timer.when();c.credit_synthesis(1);c.credit_synthesis(2)
            self.assertEqual(timer.when(),after);self.assertLessEqual(after,time.monotonic()+2);c.deadlines.clear()
        await c.close()
if __name__=='__main__':unittest.main()
