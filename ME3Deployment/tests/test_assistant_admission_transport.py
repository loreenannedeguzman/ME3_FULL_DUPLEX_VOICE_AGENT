"""WB-01.3 original RED cases: known provider exhaustion must prevent a POST."""
import unittest
from me3_assistant import groq_backend as g
from tests import test_assistant_groq_adapter as f

class AdmissionTransportTests(unittest.IsolatedAsyncioTestCase):
 async def check_denied(self,headers,status=200):
  reply=f.Reply(status);reply.headers=headers;session=f.Session(reply);events=f.Events()
  transport=g.Transport(events.emit,key='PRIVATE_FIXTURE_KEY',session=session)
  try:await transport.request('reasoning',json={'max_completion_tokens':1024})
  except g.ProviderError:
   self.assertEqual(status,429)
  session.reply=f.Reply()
  with self.assertRaisesRegex(ValueError,'provider_admission_denied'):
   await transport.request('reasoning',json={'max_completion_tokens':1024})
  self.assertEqual(len(session.calls),1);self.assertFalse(transport.active)
  self.assertNotIn('PRIVATE_FIXTURE_KEY',str(events.rows))
 async def test_observed_daily_request_exhaustion_prevents_next_post(self):
  await self.check_denied({'x-ratelimit-limit-requests':'1000','x-ratelimit-remaining-requests':'0','x-ratelimit-reset-requests':'1h'})
 async def test_observed_token_shortfall_prevents_next_post(self):
  await self.check_denied({'x-ratelimit-limit-tokens':'8000','x-ratelimit-remaining-tokens':'10','x-ratelimit-reset-tokens':'30s'})
 async def test_429_cooldown_prevents_manual_repeat_without_automatic_retry(self):
  await self.check_denied({'retry-after':'60'},429)

if __name__=='__main__':unittest.main()
