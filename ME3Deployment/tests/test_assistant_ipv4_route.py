"""Provider-free actual aiohttp resolver/session with controlled wire/addresses."""
import asyncio,json,socket,unittest
from unittest.mock import patch
from me3_assistant import groq_backend as g
from tests import test_assistant_groq_adapter as f

class RouteTests(unittest.IsolatedAsyncioTestCase):
 async def test_real_connector_passes_only_IPv4_to_system_resolution_preserves_hostname(self):
  seen=[]
  def resolve(host,port,family=0,type=0,proto=0,flags=0):
   seen.append((host,port,family))
   if family!=socket.AF_INET:raise socket.gaierror(socket.EAI_AGAIN,'fixture_combined_family_failure')
   return [(socket.AF_INET,socket.SOCK_STREAM,6,'',('192.0.2.7',port))]
  connector=g.groq_connector()
  try:
   with patch('socket.getaddrinfo',resolve):records=await connector._resolve_host('provider.fixture.invalid',443)
   self.assertEqual(seen,[('provider.fixture.invalid',443,socket.AF_INET)])
   self.assertEqual(records[0]['hostname'],'provider.fixture.invalid');self.assertEqual(records[0]['host'],'192.0.2.7')
   self.assertEqual(connector.family,socket.AF_INET);self.assertIs(connector._ssl,True)
  finally:await connector.close()
 async def test_owned_transport_both_roles_keep_one_original_hostname_request_and_clean_connector(self):
  original=g.aiohttp.ClientSession
  for role in ('asr','reasoning'):
   seen=[];posts=[];events=f.Events()
   def session(**kw):
    seen.append(kw);s=original(**kw)
    def post(url,**kwargs):posts.append((url,kwargs));return f.Reply()
    s.post=post;return s
   t=g.Transport(events.emit,key='fixture-private',opt_in=True,admission=g.Admission())
   try:
    with patch.object(g.aiohttp,'ClientSession',session):await t.request(role,**({'json':{}} if role=='reasoning' else {'data':'fixture'}))
    self.assertEqual(len(posts),1);self.assertEqual(posts[0][0],g.ENDPOINTS[role]);self.assertFalse(posts[0][1]['allow_redirects'])
    self.assertEqual(seen[0]['connector'].family,socket.AF_INET);self.assertIs(seen[0]['connector']._ssl,True);self.assertEqual(seen[0]['timeout'].total,30)
    self.assertNotIn('fixture-private',str(events.rows));self.assertFalse(t.active)
   finally:await t.close()
   self.assertTrue(seen[0]['connector'].closed)
 async def test_failed_original_request_has_no_second_family_or_wire_retry(self):
  session=f.Session(f.Reply(status=503));t=g.Transport(lambda *a,**k:None,key='fixture',session=session)
  with patch.object(g,'groq_connector') as connector,self.assertRaises(g.ProviderError):await t.request('reasoning',json={})
  self.assertEqual(len(session.calls),1);connector.assert_not_called();self.assertFalse(t.active)
