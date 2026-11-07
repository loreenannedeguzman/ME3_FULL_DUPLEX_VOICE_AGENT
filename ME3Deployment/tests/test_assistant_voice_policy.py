"""Actual normal startup hook and capacity boundary; no model/audio requests."""
import asyncio,unittest
from types import SimpleNamespace
from unittest.mock import Mock
from me3_assistant.voice_policy import install_normal_startup,reserve_voice_reasoning
from speech_mcp.client import ProviderLimited
from tests import test_completion_pipeline as pipeline_fixtures

class Budget:
 def __init__(self,n):self.n=n;self.exhausted=asyncio.Event();self.reserved=[]
 def remaining(self,role):return self.n if role=='tts' else 2
 def reserve(self,role):self.reserved.append(role);return 235

class CapacityTests(unittest.TestCase):
 def test_zero_speech_capacity_stops_before_reasoning_reservation(self):
  b=Budget(0)
  with self.assertRaises(ProviderLimited) as e:reserve_voice_reasoning(b)
  self.assertEqual(str(e.exception),'local_speech_allowance_exhausted_before_reasoning');self.assertEqual(b.reserved,[]);self.assertTrue(b.exhausted.is_set())
 def test_remaining_speech_keeps_normal_reasoning_reservation(self):
  b=Budget(1);self.assertEqual(reserve_voice_reasoning(b),235);self.assertEqual(b.reserved,['reasoning']);self.assertEqual(b.n,1)
 def test_invalid_capacity_fails_closed(self):
  for n in [True,-1,None]:
   b=Budget(n)
   with self.assertRaises(ValueError):reserve_voice_reasoning(b)
   self.assertFalse(b.reserved)

class StartupTests(unittest.IsolatedAsyncioTestCase):
 asyncSetUp=pipeline_fixtures.PipelineTests.asyncSetUp
 asyncTearDown=pipeline_fixtures.PipelineTests.asyncTearDown
 async def test_actual_pipeline_hook_avoids_unused_synthesis_but_keeps_greeting_and_ack_methods(self):
  greeting=Mock();self.p.controller.greet=greeting;cue=self.p.semantic_cue;ack=self.p.backchannel
  install_normal_startup(self.p,self.events);await self.p.startup()
  self.assertEqual(self.clients.calls,[]);greeting.assert_called_once_with()
  self.assertEqual(self.p.semantic_cue,cue);self.assertEqual(self.p.backchannel,ack)
 async def test_obsolete_or_closing_startup_cannot_greet(self):
  for interrupted in [False,True]:
   greeting=Mock();self.p.controller.greet=greeting;self.p.closing=not interrupted
   if interrupted:self.p.controller.interrupt()
   install_normal_startup(self.p,self.events);await self.p.startup();greeting.assert_not_called()
 async def test_shared_historical_startup_is_unchanged_without_normal_hook(self):
  greeting=Mock();self.p.controller.greet=greeting;await self.p.startup()
  self.assertEqual([c[0] for c in self.clients.calls],['tts']);greeting.assert_called_once_with()

if __name__=='__main__':unittest.main()
