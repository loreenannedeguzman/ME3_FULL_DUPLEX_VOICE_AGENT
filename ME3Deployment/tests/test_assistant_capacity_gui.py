"""Real local HTTP and fake clock/wire; never real audio or provider calls."""
import asyncio,json,threading,unittest
from unittest.mock import patch
from me3_assistant.admission import Admission,AdmissionDenied
from me3_assistant.groq_backend import Transport,ProviderError
from me3_assistant.groq_json import JSONGroqReasoning
from me3_assistant.gui import Console
from tests import test_assistant_gui as http_tests
from tests import test_assistant_groq_adapter as f
from tests.test_assistant_mode import config

def seeded(now,**extra):
    g=Admission(clock=lambda:now[0]);t=g.admit('reasoning',1)
    h={'x-ratelimit-limit-requests':'1000','x-ratelimit-remaining-requests':'5','x-ratelimit-reset-requests':'1h','x-ratelimit-limit-tokens':'8000','x-ratelimit-remaining-tokens':'0','x-ratelimit-reset-tokens':'16.57s',**extra}
    g.observe('reasoning',t,h,200);g.finish('reasoning',t);return g

class CapacityStateTests(unittest.TestCase):
    def test_preflight_reports_short_window_without_mutating_observations(self):
        now=[1000.];g=seeded(now);before=json.dumps(g.memory,sort_keys=True)
        self.assertAlmostEqual(g.capacity_delay('reasoning',4000,20),16.57)
        self.assertEqual(json.dumps(g.memory,sort_keys=True),before)
        with self.assertRaises(AdmissionDenied):g.admit('reasoning',4000)
    def test_no_wait_for_daily_quota_impossible_payload_or_long_window(self):
        now=[1000.]
        for h,tokens in [({'x-ratelimit-remaining-requests':'0'},4000),({},8001),({'x-ratelimit-reset-tokens':'1m'},4000)]:
            g=seeded(now,**h);self.assertEqual(g.capacity_delay('reasoning',tokens,20),0)
            with self.assertRaises(AdmissionDenied):g.admit('reasoning',tokens)
    def test_expired_or_unknown_balance_does_not_wait_or_invent_refill(self):
        now=[1000.];g=seeded(now);now[0]+=17;before=json.dumps(g.memory)
        self.assertEqual(g.capacity_delay('reasoning',4000,20),0);self.assertEqual(json.dumps(g.memory),before)
        self.assertEqual(Admission().capacity_delay('reasoning',4000,20),0)

class CapacityWireTests(unittest.IsolatedAsyncioTestCase):
    async def test_wait_then_one_original_wire_request_and_no_retry(self):
        now=[1000.];g=seeded(now);events=f.Events();s=f.Session(f.Reply());t=Transport(events.emit,key='fixture',session=s,admission=g);delays=[]
        async def sleep(delay):delays.append(delay);now[0]+=delay
        with patch('me3_assistant.groq_backend.asyncio.sleep',sleep):await t.request('reasoning',capacity_wait_seconds=20,json={})
        self.assertEqual(len(delays),1);self.assertEqual(len(s.calls),1);self.assertFalse(t.active)
        self.assertNotIn('capacity_wait_seconds',s.calls[0][1]);self.assertFalse(any(e=='groq_admission_denied' for e,k in events.rows))
        self.assertEqual(sum(e=='groq_capacity_wait_start' for e,k in events.rows),1)
    async def test_interruption_cancels_wait_before_any_wire_or_lease_and_releases_model(self):
        now=[1000.];g=seeded(now);s=f.Session(f.Reply());events=f.Events();t=Transport(events.emit,key='fixture',session=s,admission=g);m=JSONGroqReasoning(events,transport=t);entered=asyncio.Event();before=json.dumps(g.memory,sort_keys=True)
        async def sleep(delay):entered.set();await asyncio.Event().wait()
        with patch('me3_assistant.groq_backend.asyncio.sleep',sleep):
            task=asyncio.create_task(m.decide([],[],[]));await asyncio.wait_for(entered.wait(),1);task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
        self.assertFalse(m.lock.locked());self.assertFalse(t.active);self.assertEqual(s.calls,[]);self.assertEqual(json.dumps(g.memory,sort_keys=True),before)
        self.assertTrue(any(e=='groq_capacity_wait_cancelled' for e,k in events.rows))
    async def test_changed_observation_after_wait_fails_once_without_repeated_wait(self):
        now=[1000.];g=seeded(now);s=f.Session(f.Reply());t=Transport(lambda *a,**k:None,key='fixture',session=s,admission=g);waits=[]
        async def sleep(delay):
            waits.append(delay);now[0]+=delay;g.memory['roles']['reasoning']['token_reset']=now[0]+10
        with patch('me3_assistant.groq_backend.asyncio.sleep',sleep),self.assertRaises(AdmissionDenied):await t.request('reasoning',capacity_wait_seconds=20,json={})
        self.assertEqual(len(waits),1);self.assertEqual(s.calls,[])
    async def test_real_wire_failure_still_stops_without_retry_or_extra_wait(self):
        s=f.Session(f.Reply(status=429));t=Transport(lambda *a,**k:None,key='fixture',session=s)
        with patch('me3_assistant.groq_backend.asyncio.sleep') as sleep,self.assertRaises(ProviderError):await t.request('reasoning',capacity_wait_seconds=20,json={})
        self.assertEqual(len(s.calls),1);sleep.assert_not_called()
    async def test_wait_state_is_visible_without_claiming_a_completed_answer(self):
        c=Console(config(),{'revision':'fixture'});c.observe('groq_capacity_wait_start',seconds=16.57)
        self.assertEqual(c.state,'waiting_capacity');self.assertIsNone(c.public_error);self.assertEqual(c.history,[])
        c.observe('groq_capacity_wait_end');self.assertEqual(c.state,'processing')

class PollIsolationTests(unittest.TestCase):
    setUp=http_tests.HTTPTests.setUp;tearDown=http_tests.HTTPTests.tearDown;request=http_tests.HTTPTests.request;auth=http_tests.HTTPTests.auth
    def test_blocked_status_files_do_not_freeze_loop_or_stop_command(self):
        entered=threading.Event();release=threading.Event();heartbeat=threading.Event();result=[]
        def slow():entered.set();release.wait(2);return {'notice':'fixture','provider_calls':0}
        self.console.snapshot=slow
        def poll():result.append(self.request('GET','/api/state',Cookie='me3_gui='+self.server.cookie)[0])
        thread=threading.Thread(target=poll);thread.start()
        try:
            self.assertTrue(entered.wait(1));self.loop.call_soon_threadsafe(heartbeat.set)
            self.assertTrue(heartbeat.wait(.4),'status disk work blocked GUI/audio event loop')
            self.assertEqual(self.request('POST','/api/command',json.dumps({'action':'stop'}),**self.auth())[0],200)
        finally:release.set();thread.join(3)
        self.assertEqual(result,[200])
