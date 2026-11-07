import asyncio,hashlib,json,os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from me3_assistant import groq_json as g
from me3_assistant.voice_profile import normal_piper_environment,PIPER_ASSETS
from speech_mcp.tts_config import TTSConfig
from tests import test_assistant_groq_adapter as a
from tests import test_assistant_groq_json as j
from tests import test_assistant_speech as speech

class ProfileTests(unittest.IsolatedAsyncioTestCase):
 async def test_normal_reasoner_uses_explicit_current_profile_strict_schema_without_native_tools_or_retry(self):
  wire=a.fake_transport(j.answer('A fresh answer.'));model=g.JSONGroqReasoning(a.Events(),transport=wire)
  self.assertEqual((await model.decide([],j.DECL,[]))[2],'A fresh answer.')
  p=wire.request.call_args.kwargs['json'];self.assertEqual(p['model'],'qwen/qwen3.8-27b');self.assertTrue(p['response_format']['json_schema']['strict'])
  for key in ('tools','tool_choice','parallel_tool_calls'):self.assertNotIn(key,p)
  self.assertEqual(p['max_completion_tokens'],2048);self.assertEqual(wire.request.await_count,1)
 async def test_failed_new_model_never_falls_back_to_old_model(self):
  wire=a.fake_transport(None);wire.request.side_effect=a.G.ProviderError(400,'json_validate_failed');model=g.JSONGroqReasoning(a.Events(),transport=wire)
  with self.assertRaises(a.G.ProviderError):await model.decide([],j.DECL,[])
  self.assertEqual(wire.request.await_count,1);self.assertEqual(wire.request.call_args.kwargs['json']['model'],'qwen/qwen3.8-27b');self.assertFalse(model.lock.locked())
 def test_normal_voice_requires_pinned_assets_and_never_silently_falls_back(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);folder=root/'models/piper';folder.mkdir(parents=True)
   with self.assertRaisesRegex(ValueError,'normal_piper_asset_mismatch'):normal_piper_environment(root)
   for name in PIPER_ASSETS:(folder/name).write_bytes(b'corrupt or wrong model')
   before={name:(folder/name).read_bytes() for name in PIPER_ASSETS}
   with self.assertRaisesRegex(ValueError,'normal_piper_asset_mismatch'):normal_piper_environment(root)
   self.assertEqual(before,{name:(folder/name).read_bytes() for name in PIPER_ASSETS})
   with patch('me3_assistant.voice_profile.PIPER_ASSETS',{name:hashlib.sha256(raw).hexdigest() for name,raw in before.items()}):
    env=normal_piper_environment(root);self.assertEqual(Path(env['PIPER_MODEL']).name,'en_US-ljspeech-medium.onnx');self.assertEqual(env['PIPER_WORKER_MODE'],'resident')
 def test_speech_mcp_reports_actual_selected_voice_and_rejects_unapproved_models(self):
  with tempfile.TemporaryDirectory() as temp:
   for voice in ('en_US-ljspeech-medium','en_US-ljspeech-high'):
    model=Path(temp)/(voice+'.onnx');model.write_bytes(b'fixture');Path(str(model)+'.json').write_text('{}')
    c=TTSConfig.from_env(dict(TTS_PROVIDER='piper',PIPER_MODEL=str(model),PIPER_PYTHON=sys.executable));c.require_ready();self.assertEqual(c.public_info()['model'],'piper:'+voice)
   with self.assertRaisesRegex(ValueError,'piper_model_required'):TTSConfig.from_env(dict(TTS_PROVIDER='piper',PIPER_MODEL=str(Path(temp)/'unapproved.onnx')))

class FirstAudioTests(unittest.IsolatedAsyncioTestCase):
 async def test_short_conversation_prefix_plays_while_remainder_is_pending_and_cancel_discards_it(self):
  text='A conversation can explore an unfamiliar idea. We can follow that idea wherever it leads.'
  self.assertLessEqual(len(text),120);self.assertEqual(''.join(speech.response_parts(text)),text);self.assertEqual(len(speech.response_parts(text)),2)
  client=speech.Speech();client.hold=True;played=[]
  async def play(pcm,generation):played.append(generation)
  c=speech.SpeechTests.controller(self,client,play);c.spawn(c.speak(text,1,'normal'))
  await asyncio.wait_for(client.entered.wait(),1);self.assertEqual(played,[1]);self.assertFalse(c.task.done())
  c.interrupt();await asyncio.gather(c.task,return_exceptions=True)
  self.assertEqual(played,[1]);self.assertTrue(client.cancelled)
  self.assertNotIn(text,str(c.history));await c.close()
 def test_conversation_boundaries_preserve_whitespace_words_numbers_and_at_most_one_lookahead(self):
  for text in ['A new quantity is 3.14, followed by 18.5 percent and a second explanation in the same answer.','こんにちは '*30,'x'*140,'A short reply.']:
   parts=speech.response_parts(text);self.assertEqual(''.join(parts),text);self.assertLessEqual(len(parts),2)
   if len(parts)>1:self.assertLessEqual(len(parts[0]),72);self.assertFalse(parts[0].endswith('3.'))
