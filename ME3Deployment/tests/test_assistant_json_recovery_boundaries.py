"""WB-01.1: narrow rejection recovery, no retry, dispatch or leaked content."""
import asyncio,unittest
from me3_assistant.groq_backend import ProviderError
from tests import test_assistant_recovery as f
from tests import test_assistant_mode as m
from tests import test_assistant_groq_adapter as a
from tests import test_assistant_groq_json as j
from tests import test_assistant_json_recovery as r

class JSONRecoveryBoundaryTests(unittest.IsolatedAsyncioTestCase):
 asyncSetUp=m.MCPTests.asyncSetUp
 asyncTearDown=m.MCPTests.asyncTearDown
 cfg=m.MCPTests.cfg
 router=f.RecoveryTests.router
 async def test_only_the_exact_known_ValueError_is_recoverable(self):
  class ProtocolFault(ValueError):pass
  errors=[ValueError('invalid_final_speech'),ProtocolFault('invalid_final_speech'),ValueError('invalid_final_speech extra'),ValueError('other_protocol_fault'),ValueError('invalid_json_object_decision extra'),ProtocolFault('invalid_json_object_decision'),RuntimeError('invalid_json_object_decision'),ProviderError(400,'invalid_request_error'),ProviderError(401),ProviderError(403),ProviderError(429)]
  async with m.Registry(self.events,self.cfg()) as reg:
   for exc in errors:
    with self.subTest(error=type(exc).__name__,code=getattr(exc,'code',None)):
     router,model=self.router(reg,exc)
     with self.assertRaises(type(exc)) as caught:await router.answer(m.history('A request.'))
     self.assertIs(caught.exception,exc);self.assertEqual(len(model.seen),1)
   self.assertEqual(reg.calls,0);self.assertEqual(reg.journal.records,{})
   self.assertFalse(any(e['event']=='assistant_reasoning_recoverable_failure' for e in self.events.rows))
 async def test_cancellation_still_propagates_without_notice_or_dispatch(self):
  async with m.Registry(self.events,self.cfg()) as reg:
   router,model=self.router(reg,asyncio.CancelledError())
   with self.assertRaises(asyncio.CancelledError):await router.answer(m.history('A canceled request.'))
   self.assertEqual(len(model.seen),1);self.assertEqual(reg.calls,0)
   self.assertFalse(any(e['event']=='assistant_reasoning_recoverable_failure' for e in self.events.rows))
 async def test_json_rejection_after_completed_read_does_not_retry_the_read(self):
  async with m.Registry(self.events,self.cfg('extension')) as reg:
   router,transport=r.JSONRecoveryTests.router(self,reg,j.answer(calls=[{'name':'configured__count_words','arguments':{'text':'one two'}}]),a.answer('PRIVATE invalid JSON'),j.answer('A coherent new answer.'))
   first=await router.answer(m.history('Count the words.'))
   self.assertIn('No new action',first);self.assertIn('earlier completed actions were not undone',first)
   self.assertEqual(transport.request.await_count,2);self.assertEqual(reg.calls,1)
   failures=[e for e in self.events.rows if e['event']=='assistant_reasoning_recoverable_failure']
   self.assertEqual(len(failures),1);self.assertEqual(failures[0]['completed_read_steps'],1);self.assertFalse(failures[0]['automatic_retry'])
   self.assertNotIn('PRIVATE',str(self.events.rows));self.assertIsNone(router.pending)
   self.assertEqual(await router.answer(m.history('A new question',first)),'A coherent new answer.')
   self.assertEqual(reg.calls,1);self.assertEqual(transport.request.await_count,3)

if __name__=='__main__':unittest.main()
