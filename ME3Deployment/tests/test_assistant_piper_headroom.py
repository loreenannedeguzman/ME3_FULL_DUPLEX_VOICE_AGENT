"""Bounded Piper session/worker profile; fake runtime and framed IPC only."""
import asyncio,json,os,sys,tempfile,types,unittest
from pathlib import Path
from unittest.mock import patch
from speech_mcp.piper_profile import PROFILE,session_options,load_voice
from tests import test_piper_reuse as reuse

class Options:
 def __init__(self):self.entries={}
 def add_session_config_entry(self,k,v):self.entries[k]=v
 def get_session_config_entry(self,k):return self.entries[k]
class ProfileTests(unittest.TestCase):
 def runtime(self):
  return types.SimpleNamespace(SessionOptions=Options,ExecutionMode=types.SimpleNamespace(ORT_SEQUENTIAL=0))
 def test_explicit_two_threads_single_interop_sequential_and_no_spinning(self):
  opts=session_options(self.runtime())
  self.assertEqual((opts.intra_op_num_threads,opts.inter_op_num_threads,opts.execution_mode),(2,1,0))
  self.assertEqual(opts.entries,{'session.intra_op.allow_spinning':'0','session.inter_op.allow_spinning':'0'})
 def fake_modules(self,*,mismatch=False):
  runtime=self.runtime();self.loaded=[];self.voices=[]
  def session(path,*,sess_options,providers):
   self.loaded.append((path,sess_options,list(providers)))
   if mismatch:sess_options.intra_op_num_threads=8
   return types.SimpleNamespace(get_session_options=lambda:sess_options,get_providers=lambda:providers)
  runtime.InferenceSession=session
  config=types.ModuleType('piper.config');config.PiperConfig=types.SimpleNamespace(from_dict=lambda value:('original-config',value))
  piper=types.ModuleType('piper')
  def voice(**kwargs):self.voices.append(kwargs);return types.SimpleNamespace(**kwargs)
  piper.PiperVoice=voice
  return {'onnxruntime':runtime,'piper':piper,'piper.config':config}
 def test_same_model_config_and_default_voice_resources_without_synthesis_or_download(self):
  with tempfile.TemporaryDirectory() as temp:
   p=Path(temp)/'en_US-ljspeech-medium.onnx';p.write_bytes(b'FAKE MODEL');Path(str(p)+'.json').write_text('{"sample_rate":22050}')
   before=p.read_bytes();modules=self.fake_modules()
   with patch.dict(sys.modules,modules):voice=load_voice(p,PROFILE)
   self.assertEqual(voice.config,('original-config',{'sample_rate':22050}))
   self.assertEqual(set(self.voices[0]),{'config','session'});self.assertEqual(self.loaded[0][0],str(p));self.assertEqual(self.loaded[0][2],['CPUExecutionProvider']);self.assertEqual(p.read_bytes(),before)
 def test_invalid_profile_and_effective_option_mismatch_fail_without_fallback(self):
  with self.assertRaisesRegex(ValueError,'invalid_piper_cpu_profile'):load_voice('not-opened',PROFILE+'x')
  with tempfile.TemporaryDirectory() as temp:
   p=Path(temp)/'en_US-ljspeech-medium.onnx';p.write_bytes(b'FAKE');Path(str(p)+'.json').write_text('{}')
   with patch.dict(sys.modules,self.fake_modules(mismatch=True)),self.assertRaisesRegex(ValueError,'piper_cpu_profile_mismatch'):load_voice(p,PROFILE)
   self.assertEqual(len(self.loaded),1);self.assertEqual(self.voices,[])

class EnvironmentTests(unittest.IsolatedAsyncioTestCase):
 asyncSetUp=reuse.ReuseTests.asyncSetUp
 asyncTearDown=reuse.ReuseTests.asyncTearDown
 async def test_only_explicit_worker_gets_thread_caps_and_no_parent_credentials(self):
  launched=[];original=asyncio.create_subprocess_exec
  async def spawn(*args,**kwargs):launched.append(dict(kwargs['env']));return await original(*args,**kwargs)
  with patch.dict(os.environ,{'ME3_PIPER_CPU_PROFILE':PROFILE,'GROQ_API_KEY':'PRIVATE','GOOGLE_API_KEY':'PRIVATE','OPENBLAS_NUM_THREADS':'99'}),patch('speech_mcp.piper_resident.asyncio.create_subprocess_exec',spawn):
   await self.adapter.synthesize_identified('fixture','normal',1,'fixture-identity')
  self.assertEqual(len(launched),1)
  for key in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS']:self.assertEqual(launched[0][key],'1')
  self.assertEqual(launched[0]['ME3_PIPER_CPU_PROFILE'],PROFILE);self.assertNotIn('PRIVATE',str(launched));self.assertEqual(self.adapter.metrics['reuse_index'],0)
 async def test_invalid_requested_profile_never_spawns_worker_or_falls_back(self):
  with patch.dict(os.environ,{'ME3_PIPER_CPU_PROFILE':'unsupported'}),patch('speech_mcp.piper_resident.asyncio.create_subprocess_exec') as spawn:
   with self.assertRaisesRegex(ValueError,'invalid_piper_cpu_profile'):await self.adapter.synthesize('fixture','normal')
  spawn.assert_not_called();self.assertIsNone(self.adapter.process)

