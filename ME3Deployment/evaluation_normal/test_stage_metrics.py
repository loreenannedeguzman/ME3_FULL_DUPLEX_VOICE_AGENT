import unittest
from evaluation_normal.stage_metrics import first_playable
def event(name,t,**kw):return {'event':name,'monotonic_ns':int(t*1e9),'generation':1,**kw}
class FirstPlayableTests(unittest.TestCase):
    def events(self):
        return [event('mcp_request_start',0,role='tts',request_id='cue'),event('mcp_request_end',.1,role='tts',request_id='cue'),event('reasoning_end',1),event('mcp_request_start',1.1,role='tts',request_id='prefix'),event('mcp_request_end',2.1,role='tts',request_id='prefix'),event('mcp_request_start',2.11,role='tts',request_id='lookahead'),event('response_audio_ready',2.12,greeting=False,error_notice=False),event('mcp_request_end',7,role='tts',request_id='lookahead')]
    def test_lookahead_finishing_after_first_audio_is_not_first_audio_synthesis(self):
        r=first_playable(self.events(),1);self.assertEqual(r['request_id'],'prefix');self.assertEqual(r['first_response_TTS_request_seconds'],1);self.assertAlmostEqual(r['TTS_dispatch_to_first_application_PCM_ready_seconds'],1.02)
    def test_missing_prefix_completion_is_not_filled_with_cue_or_lookahead(self):
        es=[e for e in self.events() if not (e['event']=='mcp_request_end' and e['request_id']=='prefix')]
        self.assertEqual(first_playable(es,1)['status'],'NOT_MEASURED')
    def test_missing_ready_marker_or_reasoning_cannot_be_invented(self):
        for name in ('response_audio_ready','reasoning_end'):
            self.assertEqual(first_playable([e for e in self.events() if e['event']!=name],1)['status'],'NOT_MEASURED')
    def test_wrong_generation_greeting_and_error_notice_do_not_supply_ready_marker(self):
        for change in ({'generation':2},{'greeting':True},{'error_notice':True}):
            es=[dict(e,**change) if e['event']=='response_audio_ready' else e for e in self.events()]
            self.assertEqual(first_playable(es,1)['status'],'NOT_MEASURED')
    def test_request_identity_is_required(self):
        es=[dict(e,request_id='different') if e['event']=='mcp_request_end' and e['request_id']=='prefix' else e for e in self.events()]
        self.assertEqual(first_playable(es,1)['status'],'NOT_MEASURED')
if __name__=='__main__':unittest.main()
