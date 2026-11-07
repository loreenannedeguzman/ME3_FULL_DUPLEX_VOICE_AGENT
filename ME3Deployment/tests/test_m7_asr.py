import asyncio,base64,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
import anyio
from me3_m7.server import ASRService
from me3_m7.controller import resolve,incomplete,M7TurnController
from me3_m7.client import M7SpeechClients,RecoverableASRTimeout
from speech_mcp.client import SpeechClients,SpeechServiceFailed
from voice_agent import EventLog
ROOT=Path(__file__).resolve().parents[1]
PCM=bytes(6400);B64=base64.b64encode(PCM).decode()
class Events:
    def __init__(self):self.rows=[]
    def emit(self,event,**kw):self.rows.append({'event':event,**kw})
class PolicyTests(unittest.TestCase):
    def test_explicit_continuation_only(self):
        self.assertEqual(resolve('What is the capital','of France?'),('continuation','What is the capital of France?'))
        self.assertEqual(resolve('Add five pencils','of France?')[0],'ambiguous')
    def test_replacement_abandons_prefix_without_adding_words(self):
        self.assertEqual(resolve('Please tell me','Actually, what is five plus five?'),('replacement','what is five plus five?'))
    def test_incomplete_and_ambiguous_do_not_become_tasks(self):
        for x in ('Please tell me','What is the capital','What is the capital of','Add five pencils to my'):self.assertTrue(incomplete(x))
        self.assertFalse(incomplete('Add five pencils to my shopping list.'))
        for x in ('confirm','Actually, confirm','Stop, confirm'):
            self.assertEqual(resolve('Add five',x)[0],'ambiguous')
    def test_standalone_question_replaces_outstanding_complete_request(self):
        self.assertEqual(resolve('What is two plus two?','What is the capital of France?'),('replacement','What is the capital of France?'))
    def test_unrelated_suffix_never_guesses_country(self):self.assertEqual(resolve(None,'of France?'),('ambiguous',None))
class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_anyio_cancel_completes_cleanup_before_next_operation(self):
        state={'closed':0,'started':asyncio.Event()}
        class P:
            def __init__(self,emit):pass
            async def transcribe(self,pcm,*args):
                state['started'].set()
                if pcm[0]==1:await asyncio.Event().wait()
                return {'text':'ok'}
            async def close(self):await anyio.sleep(.01);state['closed']+=1
        events=[];service=ASRService(P,lambda event,**kw:events.append((event,kw)))
        async with anyio.create_task_group() as group:
            group.start_soon(service.call,base64.b64encode(b'\1'+bytes(6399)).decode(),1,'cancel-one');await state['started'].wait();group.cancel_scope.cancel()
        self.assertEqual(service.states['cancel-one']['state'],'released');self.assertTrue(service.states['cancel-one']['cleanup_ok']);self.assertEqual(state['closed'],1)
        result=await service.call(B64,2,'replacement');self.assertTrue(result['ok']);self.assertEqual(state['closed'],2);self.assertFalse(service.lock.locked())
    async def test_timeout_has_release_evidence_and_no_retry(self):
        state={'calls':0,'closed':False}
        class P:
            def __init__(self,emit):pass
            async def transcribe(self,*args):state['calls']+=1;await asyncio.Event().wait()
            async def close(self):state['closed']=True
        service=ASRService(P,lambda *a,**k:None);result=await service.call(B64,1,'timeout',deadline_ms=100)
        self.assertFalse(result['ok']);self.assertEqual(result['error_type'],'TimeoutError');self.assertTrue(result['cleanup_ok']);self.assertTrue(state['closed']);self.assertEqual(state['calls'],1)
    async def test_cleanup_failure_poisoned_no_following_provider(self):
        class P:
            def __init__(self,emit):pass
            async def transcribe(self,*args):return {'text':'ok'}
            async def close(self):raise RuntimeError('synthetic')
        service=ASRService(P,lambda *a,**k:None);result=await service.call(B64,1,'bad-close');self.assertFalse(result['ok']);self.assertTrue(service.poisoned)
        with self.assertRaisesRegex(ValueError,'cleanup'):await service.call(B64,2,'blocked')
    async def test_separate_audio_bounds_and_duplicate_identity(self):
        service=ASRService(writer=lambda *a,**k:None)
        with self.assertRaisesRegex(ValueError,'previous'):await service.call(B64,1,'bad',previous_pcm_b64=B64,previous_generation=1)
        with self.assertRaisesRegex(ValueError,'configuration'):await service.call(B64,1,'profile',profile='automatic')
    async def test_canceled_lock_waiter_does_not_release_owner(self):
        service=ASRService(writer=lambda *a,**k:None);await service.lock.acquire();task=asyncio.create_task(service.call(B64,1,'waiter'));await asyncio.sleep(.02);task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertTrue(service.lock.locked());self.assertFalse(service.poisoned);service.lock.release()
class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_cap_preserves_fatal_latch_and_no_retries(self):
        class S:
            calls=0
            async def call_tool(self,name,args,**kw):
                self.calls+=1
                return SimpleNamespace(isError=False,structuredContent={'ok':False,'server_role':'asr','generation':args['generation'],'request_id':args['request_id'],'error_type':'TimeoutError','cleanup_ok':True},content=[])
        client=M7SpeechClients(Events(),profile='low');session=S();client.sessions['asr']=session
        for i in (1,2):
            with self.assertRaises(RecoverableASRTimeout):await client.call('asr',i,pcm_b64=B64)
        with self.assertRaises(SpeechServiceFailed):await client.call('asr',3,pcm_b64=B64)
        self.assertTrue(client.failure.is_set())
        with self.assertRaises(SpeechServiceFailed):await client.call('asr',4,pcm_b64=B64)
        self.assertEqual(session.calls,3)
    async def test_barrier_waiter_cancellation_is_not_provider_failure(self):
        class S:
            async def call_tool(self,*a,**kw):await asyncio.Event().wait()
        client=M7SpeechClients(Events(),profile='low');client.pending['old']=1;client.sessions['asr']=S();task=asyncio.create_task(client.released());await asyncio.sleep(.02);task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertFalse(client.failure.is_set());self.assertIn('old',client.pending)
    async def test_exit_does_not_suppress_cleanup_failure(self):
        client=M7SpeechClients(Events(),profile='low');client.pending['old']=1;client.released=AsyncMock(side_effect=ValueError('cleanup'))
        with patch.object(SpeechClients,'__aexit__',AsyncMock(return_value=False)):
            with self.assertRaisesRegex(ValueError,'cleanup'):await client.__aexit__(None,None,None)
    async def test_actual_mcp_cancel_release_barrier_and_replacement(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as temp:
            events=EventLog(Path(temp)/'events')
            try:
                async with M7SpeechClients(events,module='tests.m7_mcp_fixture',profile='low') as client:
                    task=asyncio.create_task(client.call('asr',1,pcm_b64=base64.b64encode(b'\1'+bytes(6399)).decode()))
                    await asyncio.sleep(.2);task.cancel()
                    with self.assertRaises(asyncio.CancelledError):await task
                    result=await client.call('asr',2,pcm_b64=B64);self.assertEqual(result['text'],'synthetic replacement');self.assertFalse(client.pending)
            finally:events.close()
            data=[json.loads(x) for x in events.path.read_text().splitlines()];barrier=next(i for i,x in enumerate(data) if x['event']=='m7_release_barrier_complete');start=next(i for i,x in enumerate(data) if x['event']=='mcp_request_start' and x['generation']==2);self.assertLess(barrier,start)
class ControllerTests(unittest.IsolatedAsyncioTestCase):
    def setup_controller(self,final,timeout=False,late=False):
        self.events=Events();self.answers=[];self.played=[];started=asyncio.Event();calls=[]
        class Clients:
            async def call(inner,role,generation,**kw):
                calls.append((role,generation,kw))
                if role=='tts':return {'pcm_b64':base64.b64encode(bytes(960)).decode()}
                if generation==1:
                    started.set()
                    if timeout:raise RecoverableASRTimeout('synthetic')
                    try:await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        if late:return {'text':'Obsolete request.'}
                        raise
                return {'text':final,'previous_text':'What is the capital','previous_generation':1}
        outer=self
        class Reasoner:
            pending={'old':'unconfirmed'}
            async def answer(inner,history):outer.answers.append(history[-1]['content'][0]['text']);return 'Replacement reply.'
        async def play(pcm,g):self.played.append(g)
        controller=M7TurnController(Clients(),Reasoner(),self.events,play);return controller,started,calls
    async def test_canceled_prefix_continues_without_pcm_concatenation(self):
        c,started,calls=self.setup_controller('of France?');c.submit(PCM,c.interrupt());await started.wait();old=c.task;c.submit(PCM,c.interrupt());await c.task;await asyncio.gather(old,return_exceptions=True)
        self.assertEqual(self.answers,['What is the capital of France?']);second=next(k for role,g,k in calls if role=='asr' and g==2);self.assertEqual(base64.b64decode(second['pcm_b64']),PCM);self.assertEqual(base64.b64decode(second['previous_pcm_b64']),PCM);await c.close()
    async def test_replacement_and_late_result_fenced(self):
        c,started,calls=self.setup_controller('Actually, what is five plus five?',late=True);c.submit(PCM,c.interrupt());await started.wait();old=c.task;c.submit(PCM,c.interrupt());await c.task;await old
        self.assertEqual(self.answers,['what is five plus five?']);self.assertEqual(self.played,[2]);self.assertTrue(any(x['event']=='stale_generation_rejected' for x in self.events.rows));await c.close()
    async def test_ambiguous_confirmation_never_calls_reasoning_and_clears_pending(self):
        c,started,calls=self.setup_controller('Confirm');c.submit(PCM,c.interrupt());await started.wait();old=c.task;c.submit(PCM,c.interrupt());await c.task;await asyncio.gather(old,return_exceptions=True)
        self.assertEqual(self.answers,[]);self.assertIsNone(c.provider.pending);await c.close()
    async def test_clean_timeout_then_explicit_next_utterance_recovers(self):
        c,started,calls=self.setup_controller('What is two plus two?',timeout=True);c.submit(PCM,c.interrupt());await c.task;c.submit(PCM,c.interrupt());await c.task
        self.assertEqual(self.answers,['What is two plus two?']);self.assertEqual(self.played,[1,2]);await c.close()

class PostBenchmarkPolicyTests(unittest.TestCase):
    def test_bare_recognized_name_uses_only_recognized_words(self):
        self.assertEqual(resolve('What is the capital','France?'),('continuation','What is the capital France?'))
        self.assertEqual(resolve('What is the capital','United Kingdom?'),('continuation','What is the capital United Kingdom?'))
    def test_commands_and_unknown_fragments_are_not_name_continuations(self):
        for fragment in ('Confirm','Delete Documents','Please stop','Two notebooks','France is lovely'):
            self.assertEqual(resolve('What is the capital',fragment)[0],'ambiguous')
        self.assertEqual(resolve('What is the capital','What is five plus five?')[0],'replacement')
class EmptyFragmentRecoveryTests(unittest.IsolatedAsyncioTestCase):
    setup_controller=ControllerTests.setup_controller
    async def test_empty_suffix_requests_repetition_without_executing_prefix(self):
        c,started,calls=self.setup_controller('');c.submit(PCM,c.interrupt());await started.wait();old=c.task;c.submit(PCM,c.interrupt());await c.task;await asyncio.gather(old,return_exceptions=True)
        self.assertEqual(self.answers,[]);self.assertEqual(self.played,[2]);self.assertIsNone(c.provider.pending)
        self.assertTrue(any(x['event']=='m7_empty_current_with_retained_prefix' for x in self.events.rows));self.assertTrue(c.task.done());await c.close()
    async def test_bare_name_after_canceled_asr_preserves_known_prefix(self):
        c,started,calls=self.setup_controller('France?');c.submit(PCM,c.interrupt());await started.wait();old=c.task;c.submit(PCM,c.interrupt());await c.task;await asyncio.gather(old,return_exceptions=True)
        self.assertEqual(self.answers,['What is the capital France?']);self.assertEqual(self.played,[2]);await c.close()
