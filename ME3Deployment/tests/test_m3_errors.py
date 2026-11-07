import base64,unittest
from types import SimpleNamespace
from speech_mcp.errors import validation_reason,known_validation_reason
from speech_mcp.contracts import normalize_tts_audio,pcm_to_wav,human_turn_limit
from speech_mcp.client import SpeechClients

class ValidationReasonTests(unittest.TestCase):
    def test_internal_reason_is_allowlisted_without_other_payloads(self):
        self.assertEqual(validation_reason(ValueError('tts_duration_limit')),'tts_duration_limit')
        for exc in [ValueError('private-canary'),ValueError({'secret':'private-canary'}),RuntimeError('tts_duration_limit'),ValueError('tts_duration_limit','private-canary')]:
            with self.subTest(kind=type(exc).__name__):self.assertEqual(validation_reason(exc),'unclassified')
        for value in [None,{},'private-canary',True]:self.assertEqual(known_validation_reason(value),'unclassified')
    def test_long_tts_is_still_rejected_with_named_reason(self):
        with self.assertRaises(ValueError) as caught:
            normalize_tts_audio(pcm_to_wav(bytes(24000*2*46),24000),'audio/wav')
        self.assertEqual(validation_reason(caught.exception),'tts_duration_limit')
    def test_turn_cap_can_only_reduce_the_existing_budget(self):
        for value in ['1','3','8']:self.assertEqual(human_turn_limit(value),int(value))
        for value in ['0','9','480','-1',' 3','03','private-canary',True,None]:
            with self.subTest(value=value),self.assertRaises(ValueError):human_turn_limit(value)

class ClientReasonTests(unittest.IsolatedAsyncioTestCase):
    async def test_mcp_error_reason_is_sanitized_at_client_boundary(self):
        class Events:
            def __init__(self):self.rows=[]
            def emit(self,event,**fields):self.rows.append({'event':event,**fields})
        class Session:
            reason='tts_duration_limit'
            async def call_tool(self,*args,**kwargs):return SimpleNamespace(isError=False,structuredContent={'ok':False,'error':'provider_error','error_type':'ValueError','validation_reason':self.reason},content=[])
        events=Events();clients=SpeechClients(events);session=Session();clients.sessions['tts']=session
        for reason,expected in [('tts_duration_limit','tts_duration_limit'),('private-canary','unclassified')]:
            session.reason=reason
            clients=SpeechClients(events);clients.sessions['tts']=session
            with self.assertRaises(ValueError):await clients.call('tts',1,text='Test')
            self.assertEqual([x for x in events.rows if x['event']=='mcp_request_error'][-1]['validation_reason'],expected)
        self.assertNotIn('private-canary',repr(events.rows))


class CallbackEvidenceTests(unittest.TestCase):
    def test_epoch_proves_ack_even_when_logging_time_is_later(self):
        from scripts.m3_evidence import callback_continuity
        clear={'epoch':4,'capture_count':10,'monotonic_ns':200}
        ack={'epoch':4,'capture_count':11,'callback_ns':100}
        capture={'capture_count':12,'callback_ns':150}
        self.assertEqual(callback_continuity(clear,[ack],[capture]),(ack,capture))
    def test_stale_epoch_or_nonadvancing_capture_is_not_accepted(self):
        from scripts.m3_evidence import callback_continuity
        clear={'epoch':4,'capture_count':10}
        self.assertEqual(callback_continuity(clear,[{'epoch':3,'capture_count':11}],[{'capture_count':10}]),(None,None))
