"""Provider-free player lifecycle, security and official metadata contracts."""
import asyncio,json,os,secrets,tempfile,threading,unittest,uuid
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,patch
from me3_assistant.youtube import PlayerBridge,BridgeClient,digest,intent,video_id,read_video,search
from me3_assistant.youtube_server import Service
from me3_assistant.youtube_action import YouTubeControl
VID='M7lc1UVf-VE'

class ParsingTests(unittest.TestCase):
 def test_only_real_ID_or_supported_HTTPS_YouTube_link(self):
  for link in [VID,'https://youtu.be/'+VID,'https://www.youtube.com/watch?v='+VID+'&t=12','https://music.youtube.com/watch?v='+VID,'https://youtube.com/shorts/'+VID]:self.assertEqual(video_id(link),VID)
  for link in ['https://youtube.com.evil.test/watch?v='+VID,'https://youtube.com@evil.test/watch?v='+VID,'http://youtube.com/watch?v='+VID,'https://127.0.0.1/watch?v='+VID,'file:///etc/passwd','https://youtube.com:444/watch?v='+VID,'some title','javascript:alert(1)']:
   with self.assertRaises(ValueError):video_id(link)
 def test_control_arguments_exact_and_volume_absolute(self):
  self.assertEqual(intent('play',VID),{'command':'play','video_id':VID,'volume':33});self.assertEqual(intent('volume',None,100.),{'command':'volume','volume':100})
  for args in [('play',VID,101),('stop',VID,None),('pause',None,1),('volume',None,True),('guess',None,None)]:
   with self.assertRaises(ValueError):intent(*args)

class SchemaEquivalenceTests(unittest.TestCase):
 def test_factored_schema_preserves_every_command_field_combination(self):
  from jsonschema import Draft202012Validator
  from me3_assistant.youtube_action import YouTubeControl
  from types import SimpleNamespace
  import itertools
  adapter=object.__new__(YouTubeControl);adapter.spec={'name':'propose_youtube_control'};new=Draft202012Validator(adapter.declaration()['parameters']);branches=[]
  for cmd,fields in [('play',['video']),('pause',[]),('resume',[]),('stop',[]),('volume',['volume'])]:
   permitted={'command',*fields}|({'volume'} if cmd=='play' else set());props={'command':{'type':'string','enum':[cmd]}}
   if 'video' in permitted:props['video']={'type':'string','minLength':1,'maxLength':500}
   if 'volume' in permitted:props['volume']={'type':'integer','minimum':0,'maximum':100}
   branches.append({'type':'object','properties':props,'required':['command',*fields],'additionalProperties':False})
  old=Draft202012Validator({'type':'object','anyOf':branches})
  for command,video,volume in itertools.product(['play','pause','resume','stop','volume','bad'],['missing',VID,'',None],['missing',0,100,101,True,None]):
   args={'command':command}
   if video!='missing':args['video']=video
   if volume!='missing':args['volume']=volume
   self.assertEqual(old.is_valid(args),new.is_valid(args),args)
  self.assertFalse(new.is_valid({'command':'stop','operation_id':'injected'}))

class BrokerTests(unittest.IsolatedAsyncioTestCase):
 async def setup_bridge(self):
  b=PlayerBridge();client=uuid.uuid4().hex;b.poll(client);b.report(client,dict(state='cued',video_id=VID,volume=33,visible=True,error=None));return b,client
 async def respond(self,b,client,command,state,volume=33):
  task=asyncio.create_task(b.execute(command,'a'*32,uuid.uuid4().hex,timeout=.2));await asyncio.sleep(0);pending=b.poll(client)['command'];b.report(client,dict(command_id=pending['id'],state=state,video_id=VID,volume=volume,visible=True,error=None));return await task
 async def test_play_pause_volume_stop_and_fresh_inspection(self):
  b,c=await self.setup_bridge()
  for clean,state,volume in [(intent('play',VID),'playing',33),(intent('pause'),'paused',33),(intent('volume',None,100),'paused',100),(intent('resume'),'playing',100),(intent('stop'),'stopped',100)]:
   r=await self.respond(b,c,clean,state,volume);self.assertEqual(r['state'],'COMPLETED');self.assertTrue(r['snapshot']['fresh'])
  seq=b.sequence;r=await self.respond(b,c,{'command':'inspect'},'stopped',100);self.assertGreater(r['snapshot']['sequence'],seq)
 async def test_blocked_autoplay_is_not_verified_and_duplicate_never_replays(self):
  b,c=await self.setup_bridge();op=uuid.uuid4().hex;t=asyncio.create_task(b.execute(intent('play',VID),'a'*32,op));await asyncio.sleep(0);p=b.poll(c)['command']
  b.report(c,dict(command_id=p['id'],state='blocked',video_id=VID,volume=33,visible=True,error='autoplay_blocked'))
  with self.assertRaises(ValueError):await t
  self.assertIsNone(b.pending);self.assertEqual((await b.execute(intent('play',VID),'a'*32,op))['state'],'UNCERTAIN');self.assertIsNone(b.pending)
 async def test_cancel_or_missing_browser_does_not_retry(self):
  b,c=await self.setup_bridge();op=uuid.uuid4().hex;t=asyncio.create_task(b.execute(intent('play',VID),'a'*32,op));await asyncio.sleep(0);t.cancel()
  with self.assertRaises(asyncio.CancelledError):await t
  self.assertIsNone(b.pending);self.assertEqual(b.operations[op]['state'],'UNCERTAIN')
  fresh=PlayerBridge()
  with self.assertRaises(ValueError):await fresh.execute(intent('play',VID),'a'*32,uuid.uuid4().hex)
 async def test_voice_gate_and_stale_report_cannot_claim_playback(self):
  b,c=await self.setup_bridge();b.voice_active=lambda:True
  with self.assertRaises(ValueError):await b.execute(intent('play',VID),'a'*32,uuid.uuid4().hex)
  self.assertIsNone(b.pending);b.seen-=4;self.assertFalse(b.public()['fresh']);new=uuid.uuid4().hex;b.poll(new);self.assertEqual(b.public()['state'],'unavailable')
  with self.assertRaises(ValueError):b.report(c,dict(state='playing',video_id=VID,volume=33,visible=True,error=None))
 async def test_service_single_dispatch_and_uncertain_no_replay(self):
  bridge=NS(call=AsyncMock(side_effect=ValueError('fixture')));s=Service(bridge);op=uuid.uuid4().hex;p=s.prepare('play',VID,None,op)
  with self.assertRaises(ValueError):await s.commit(op,p['token'],'confirm')
  self.assertEqual(s.operations[op]['state'],'UNCERTAIN');self.assertTrue((await s.commit(op,p['token'],'confirm'))['duplicate']);self.assertEqual(bridge.call.await_count,1)

class MetadataTests(unittest.IsolatedAsyncioTestCase):
 async def test_oembed_uses_official_metadata_only_and_never_injects_HTML(self):
  request=AsyncMock(return_value={'title':'Test title','author_name':'Test channel','html':'<script>do not execute</script>'});r=await read_video('https://youtu.be/'+VID,request)
  self.assertEqual(r['video_id'],VID);self.assertNotIn('html',r);self.assertFalse(r['playback_started']);self.assertEqual(request.await_count,1)
 async def test_missing_search_key_is_explicit_and_spends_nothing(self):
  with tempfile.TemporaryDirectory() as d:
   req=AsyncMock();r=await search('a new artist',request=req,key_path=Path(d)/'absent');self.assertFalse(r['available']);req.assert_not_awaited()
 async def test_search_uses_exact_API_key_header_transport_contract_and_real_result_ids(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'key';p.write_text('YOUTUBE_API_KEY='+'a'*30);p.chmod(0o600)
   req=AsyncMock(return_value={'items':[{'id':{'videoId':VID},'snippet':{'title':'Actual result','channelTitle':'Actual channel'}}]});r=await search('held out title',request=req,key_path=p)
   self.assertEqual(r['videos'][0]['video_id'],VID);self.assertEqual(req.await_count,1);self.assertEqual(req.call_args.args[0],'https://www.googleapis.com/youtube/v3/search');self.assertEqual(req.call_args.kwargs['key'],'a'*30);self.assertNotIn('a'*30,json.dumps(r))

class ActualMCPBridgeTests(unittest.IsolatedAsyncioTestCase):
 async def test_real_MCP_HTTP_bridge_player_ack_and_independent_readback(self):
  from task_mcp.client import ROOT
  from me3_assistant.gui import Console,Server
  from me3_assistant.youtube_gui import install_bridge
  from me3_assistant.registry import Registry
  from me3_assistant.actions import Actions,Journal
  from me3_assistant.standing_music import initialize
  from mcp import ClientSession,StdioServerParameters
  from mcp.client.stdio import stdio_client
  from speech_mcp.client import SpeechClients
  from tests.test_assistant_mode import FixtureBudget
  from tests.test_assistant_groq_adapter import Events
  ROOT.joinpath('.tmp').mkdir(exist_ok=True)
  with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as d:
   folder=Path(d);c=Console({'sha256':'fixture'},{'revision':'fixture'});c.gate=lambda *a,**k:({'routine_youtube_music':True},{})
   server=Server(('127.0.0.1',0),c,asyncio.get_running_loop());path=install_bridge(server,folder);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();client=uuid.uuid4().hex;c.youtube.poll(client)
   current=dict(state='cued',video_id=VID,volume=33,visible=True,error=None);c.youtube.report(client,current);played=[]
   async def browser():
    seen=None
    while True:
     p=c.youtube.poll(client)['command']
     if p and p['id']!=seen:
      seen=p['id'];cmd=p['command'];played.append(cmd)
      if cmd in {'play','resume'}:current['state']='playing'
      elif cmd=='pause':current['state']='paused'
      elif cmd=='stop':current['state']='stopped'
      if 'volume'in p:current['volume']=p['volume']
      c.youtube.report(client,{**current,'command_id':seen})
     await asyncio.sleep(.005)
   worker=asyncio.create_task(browser())
   try:
    events=Events();cfg={'servers':[{'id':'youtube','module':'me3_assistant.youtube_server','args':['--bridge-config',str(path)],'identity':'me3','role':'utility','read_tools':['get_player_status'],'rpc_timeout_seconds':20}],'actions':[{'name':'propose_youtube_control','server':'youtube','adapter':'youtube_control'}],'runtime':{'rpc_calls':128}}
    async with Registry(events,cfg) as registry:
     grant={'profile':'standing','routine_local_music':True,'routine_youtube_music':True,'allowed_actions':['propose_music_control','propose_youtube_control'],'workspace_access':False,'operation_state_path':str(folder/'state.json')};counter=folder/'standing-music-actions.json';initialize(counter);registry.session_grant=grant;registry.standing_music_counter=counter
     registry.ensure_music_output=AsyncMock(side_effect=AssertionError('YouTube must not open local ALSA output'));budget=NS(permitted=lambda role:True,reserve=lambda role:1)
     actions=Actions(registry,cfg['actions'],budget,Journal(folder/'journal.jsonl'),events,lambda g:True,allowed=['propose_youtube_control'])
     for args in [{'command':'play','video':'https://youtu.be/'+VID},{'command':'volume','volume':100},{'command':'pause'},{'command':'resume'},{'command':'stop'}]:
      result=await actions.propose('propose_youtube_control',args,1);self.assertNotIn('could not',result);self.assertIsNone(actions.pending)
     registry.ensure_music_output.assert_not_awaited();self.assertEqual(json.loads(counter.read_bytes())['music_control'],5);self.assertFalse(actions.journal.unresolved());self.assertTrue(all(r['state']=='VERIFIED' and 'volatile_music_owner' not in r for r in actions.journal.records.values()));self.assertEqual(played.count('play'),1);self.assertEqual(played.count('inspect'),5)
    self.assertEqual(current['state'],'stopped')
   finally:
    worker.cancel();await asyncio.gather(worker,return_exceptions=True);await asyncio.to_thread(server.shutdown);server.server_close();thread.join(2)

class GUIBoundaryTests(unittest.TestCase):
 def setUp(self):
  from tests.test_assistant_gui import HTTPTests
  HTTPTests.setUp(self)
 def tearDown(self):
  from tests.test_assistant_gui import HTTPTests
  HTTPTests.tearDown(self)
 def request(self,*args,**kw):
  from tests.test_assistant_gui import HTTPTests
  return HTTPTests.request(self,*args,**kw)
 def test_player_script_and_YouTube_only_frame_policy_are_served(self):
  status,body,headers=self.request('GET','/youtube.js');self.assertEqual(status,200);self.assertIn(b'onYouTubeIframeAPIReady',body);self.assertIn('javascript',headers['Content-Type']);self.assertEqual(headers['Referrer-Policy'],'strict-origin-when-cross-origin');self.assertIn('frame-src https://www.youtube.com;',headers['Content-Security-Policy']);self.assertIn("frame-ancestors 'none'",headers['Content-Security-Policy'])
 def test_private_bridge_rejects_browser_origin_and_wrong_bearer(self):
  self.server.youtube_token='a'*64
  base={'Content-Type':'application/json','Authorization':'Bearer '+'b'*64}
  self.assertEqual(self.request('POST','/api/youtube-bridge',json.dumps({'action':'status'}),**base)[0],403)
  base['Authorization']='Bearer '+'a'*64;base['Origin']=f'http://127.0.0.1:{self.port}'
  self.assertEqual(self.request('POST','/api/youtube-bridge',json.dumps({'action':'status'}),**base)[0],403)

if __name__=='__main__':unittest.main()
