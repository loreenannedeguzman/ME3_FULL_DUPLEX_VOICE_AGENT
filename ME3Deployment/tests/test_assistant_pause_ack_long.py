import asyncio,base64,hashlib,json,os,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from me3_assistant.overlap import acknowledgment_surface,attach
from me3_completion.policy import listener_ack
from me3_assistant.runner import runtime_profile
from me3_agent.speech import AgentSpeechClients
from me3_assistant.worker_readiness import prepare_speech_worker
from voice_agent import ROOT,EventLog
from tests import test_agent as fixtures
from tests import long_input_mcp_fixture as long_fixture

class SurfaceTests(unittest.TestCase):
 def test_ASR_whitespace_variants_of_noncommand_sounds_are_equivalent(self):
  for text in ['uh huh','Uh  huh.','“mm hmm”','mm\thm','¿UH HUH?']:
   with self.subTest(text=text):self.assertTrue(listener_ack(acknowledgment_surface(text),.6));self.assertFalse(listener_ack(acknowledgment_surface(text),1.))
 def test_additional_words_and_commands_never_become_listener_acknowledgments(self):
  for text in ['uh huh stop','not mm hmm','mm hmm cancel','yes','confirm','wait','no','cancel','stop','uh, huh','okay stop']:
   self.assertEqual(acknowledgment_surface(text),text);self.assertFalse(listener_ack(acknowledgment_surface(text),.5))
 def test_runtime_profile_matches_active_model_and_normal_limits_without_affecting_legacy(self):
  p=runtime_profile('groq');self.assertEqual(p['reasoning_model'],'qwen/qwen3.8-27b');self.assertEqual(p['tts_model'],'piper:en_US-ljspeech-medium');self.assertEqual((p['max_input_seconds'],p['max_transcript_chars']),(120,4000))
  old=runtime_profile('google');self.assertEqual((old['max_input_seconds'],old['max_transcript_chars']),(15,1000))
  with self.assertRaises(ValueError):runtime_profile('unapproved')

class OverlapTests(fixtures.OverlapTests):
 def setup_processor(self):
  events=super().setup_processor();attach(self.p,self.client,self.provider,events);return events
 async def test_split_sound_resumes_original_response_and_never_reserves_second_ASR(self):
  self.setup_processor();self.text='Uh huh.';calls=[];original=self.client.call
  async def call(*args,**kwargs):calls.append(args);return await original(*args,**kwargs)
  self.client.call=call;await self.p.classify_overlap(1,b'audio',.6,3,self.old_task)
  self.assertTrue(self.resumed);self.assertEqual(self.g,3);self.assertEqual(len(calls),1);self.assertFalse(any(c[0] in {'submit','takeover'} for c in self.calls))
 async def test_pending_action_retains_original_split_recognition_and_cannot_resume(self):
  self.setup_processor();self.text='Uh huh.';self.provider.pending={'phase':'confirm'}
  await self.p.classify_overlap(1,b'audio',.6,3,self.old_task)
  self.assertFalse(self.resumed);self.assertEqual(self.calls[-1],('submit',4,'Uh huh.'))

class LongInputTests(unittest.IsolatedAsyncioTestCase):
 async def test_actual_MCP_client_service_and_ASR_adapter_retain_complete22second_input_and_long_text(self):
  pcm=b'\x01\x00'+bytes(22*32000-4)+b'\x02\x00';digest=hashlib.sha256(pcm).hexdigest()
  with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as temp:
   events=EventLog(Path(temp)/'logs');profile=runtime_profile('groq')
   try:
    class Client(AgentSpeechClients):
     max_utterance_seconds=profile['max_input_seconds'];max_transcript_chars=profile['max_transcript_chars']
    async with Client(events,module='tests.long_input_mcp_fixture') as client:
     await prepare_speech_worker(client)
     data=await client.call('asr',1,pcm_b64=base64.b64encode(pcm).decode(),sample_rate=16000)
     self.assertEqual(data['text'],long_fixture.TEXT);self.assertGreater(len(data['text']),1000);self.assertTrue(data['cleanup_ok']);self.assertEqual(data['generation'],1)
    lines=(Path(temp)/'logs/asr-server.stderr.log').read_text().splitlines();proof=[json.loads(line) for line in lines if line.startswith('{') and 'fixture_full_pcm_received' in line]
    self.assertEqual([(p['bytes'],p['sha256'],p['hosted_calls']) for p in proof],[(len(pcm),digest,0)])
   finally:events.close()
 async def test_over_limit_input_is_rejected_before_MCP_or_provider_dispatch(self):
  with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as temp:
   events=EventLog(Path(temp)/'logs')
   try:
    class Client(AgentSpeechClients):max_utterance_seconds=120;max_transcript_chars=4000
    c=Client(events,module='tests.long_input_mcp_fixture')
    with self.assertRaises(ValueError):await c.call('asr',1,pcm_b64=base64.b64encode(bytes(120*32000+2)).decode(),sample_rate=16000)
    self.assertEqual(c.sessions,{})
   finally:events.close()
