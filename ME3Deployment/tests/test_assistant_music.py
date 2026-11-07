import array,asyncio,hashlib,json,math,os,shutil,struct,sys,tempfile,time,unittest,uuid,wave
from datetime import datetime,timedelta,timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
from task_mcp.client import ROOT
from me3_assistant.music import Catalog,intent,fingerprint,reap_decoder
from me3_assistant.music_audio import MusicBus,CAPACITY,attach_music
from me3_assistant.local_actions import LocalActionBudget,initialize_local_budget,validate_local_actions
from me3_assistant.config import load
from me3_assistant.registry import Registry,ToolFailure
from me3_assistant.actions import Actions,Journal
from me3_assistant.router import Router
from tests.test_assistant_mode import Events,FixtureBudget,Model,decision,history

def grant(n=6):
    return {'allowed_actions':['propose_music_control'],'local_action_limits':{'music_control':n},'expires_at_utc':(datetime.now(timezone.utc)+timedelta(minutes=10)).isoformat()}
def tone(path,duration=5):
    with wave.open(str(path),'wb') as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(16000)
        f.writeframes(b''.join(struct.pack('<h',int(1000*math.sin(2*math.pi*220*i/16000))) for i in range(int(16000*duration))))

class LocalTests(unittest.TestCase):
    def setUp(self):
        (ROOT/'.tmp').mkdir(exist_ok=True);self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='music-unit-');self.root=Path(self.tmp.name);self.bus=MusicBus(self.root/'bus',True)
    def tearDown(self):self.bus.close(destroy=True);self.tmp.cleanup()
    def test_inactive_voice_bit_exact_pause_holds_music_and_stop_fences_old_chunks(self):
        voice=struct.pack('<h',1000)*1920;self.assertEqual(self.bus.mix(voice),voice)
        epoch=self.bus.start(20);pcm=struct.pack('<h',2000)*1920;self.assertEqual(self.bus.offer(pcm,epoch),'accepted')
        self.bus.control(state=2);self.assertEqual(self.bus.mix(voice),voice);self.assertEqual(self.bus.snapshot()['buffered_bytes'],len(pcm))
        self.bus.control(state=1);self.assertNotEqual(self.bus.mix(bytes(len(pcm))),bytes(len(pcm)))
        self.bus.control(state=0);self.assertEqual(self.bus.offer(pcm,epoch),'stale');self.assertEqual(self.bus.mix(voice),voice)
    def test_voice_duck_user_mute_clipping_and_muted_position(self):
        epoch=self.bus.start(60);pcm=struct.pack('<h',32000)*1920;voice=struct.pack('<h',32000)*1920
        self.bus.offer(pcm,epoch);mixed=self.bus.mix(voice);self.assertEqual(struct.unpack('<h',mixed[:2])[0],32767)
        self.bus.offer(pcm,epoch);self.assertEqual(self.bus.mix(voice,True),voice)
        self.bus.control(volume=0);self.bus.offer(pcm,epoch);self.assertEqual(self.bus.mix(voice),voice);self.assertGreater(self.bus.snapshot()['consumed_frames'],0)
    def test_ring_bound_and_nonblocking_callback(self):
        e=self.bus.start(20);self.assertEqual(self.bus.offer(bytes(CAPACITY),e),'accepted');self.assertEqual(self.bus.offer(bytes(4),e),'full')
        self.bus.thread_lock.acquire()
        try:self.assertEqual(self.bus.mix(bytes(3840)),bytes(3840))
        finally:self.bus.thread_lock.release()
        with self.assertRaises(ValueError):self.bus.offer(b'x',e)
    def test_optional_hook_preserves_original_flag_voice_and_vad_and_isolates_bus_failure(self):
        voice=bytes(3840);post=[];out=SimpleNamespace(callback=lambda *a:(voice,7));proc=SimpleNamespace(vad=SimpleNamespace(analyze_audio=AsyncMock(return_value=SimpleNamespace(name='QUIET'))))
        transport=SimpleNamespace(output=lambda:out,state=SimpleNamespace(post=lambda e,**kw:post.append(e)))
        attach_music((None,None,transport,proc),SimpleNamespace(output_rate=48000,output_channels=2),self.bus)
        self.assertEqual(out.callback(),(voice,7));self.bus.mem[:8]=b'corrupt!';self.assertEqual(out.callback(),(voice,7));self.assertIn('music_mix_disabled',post)
        self.bus.mem[:8]=b'ME3MUS1!'
    def test_missing_catalog_and_path_escape_no_creation(self):
        root=self.root/'absent';self.assertFalse(Catalog(root).list()['available']);self.assertFalse(root.exists())
        for name in ('../a.wav','https://x/a.mp3','C:a.wav','.a.mp3','a.m3u','x.wav:stream','CON.wav'):
            with self.subTest(name=name),self.assertRaises(ValueError):Catalog(self.root).open(name)
    def test_catalog_ignores_links_and_unsupported_files(self):
        p=self.root/'Sound.wav';tone(p,.05);(self.root/'list.m3u').write_text('https://invalid',encoding='utf-8')
        self.assertEqual(Catalog(self.root).list()['tracks'][0]['track_id'],'Sound.wav');f,v=Catalog(self.root).open('Sound.wav');f.close();self.assertGreater(v['size'],0)
        os.link(p,self.root/'Link.wav');self.assertEqual(Catalog(self.root).list()['tracks'],[])
    def test_control_combinations_and_player_only_volume_bounds(self):
        self.assertEqual(intent('play','Song.mp3')['volume'],33)
        for args in [('play',None,None),('pause','Song.wav',None),('volume',None,True),('volume',None,101),('resume',None,20),('shell',None,None)]:
            with self.subTest(args=args),self.assertRaises(ValueError):intent(*args)
    def test_local_action_ledger_shared_across_restarts_without_legacy_reservations(self):
        base=FixtureBudget(0);g=grant(2);directory=self.root/'ledgers';initialize_local_budget(g,directory);b=LocalActionBudget(base,g,directory)
        self.assertEqual(b.reserve('music_control'),1);second=LocalActionBudget(base,g,directory);self.assertEqual(second.reserve('music_control'),2);self.assertFalse(b.permitted('music_control'));self.assertEqual(base.used,0)
        with self.assertRaises(FileExistsError):initialize_local_budget(g,directory)
        with self.assertRaises(ValueError):second.reserve('music_control')
    def test_missing_decreased_expired_or_changed_grant_ledgers_fail_closed(self):
        base=FixtureBudget(0);g=grant();directory=self.root/'ledgers';b=LocalActionBudget(base,g,directory)
        with self.assertRaises(ValueError):b.permitted('music_control')
        p=initialize_local_budget(g,directory);b.reserve('music_control');data=json.loads(p.read_text());data['music_control']=0;p.write_text(json.dumps(data))
        with self.assertRaises(ValueError):b.permitted('music_control')
        self.assertFalse(LocalActionBudget(base,{},directory).permitted('music_control'))
        g2=grant();g2['expires_at_utc']=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat();initialize_local_budget(g2,directory)
        with self.assertRaises(ValueError):LocalActionBudget(base,g2,directory).permitted('music_control')
    def test_local_allowance_validation(self):
        cfg={'actions':[{'name':'propose_music_control','adapter':'music_control'}]};validate_local_actions(grant(),cfg)
        for caps in ({'music_control':True},{'music_control':17},{'other':1}):
            with self.assertRaises(ValueError):validate_local_actions({**grant(),'local_action_limits':caps},cfg)
        with self.assertRaises(ValueError):validate_local_actions({**grant(),'allowed_actions':[]},cfg)
    def test_voice_authorization_requires_provisioned_music_counter_before_opening_anything(self):
        from me3_assistant.guard import authorize
        from tests.test_assistant_mode import ConfigTests
        fixture=ConfigTests();fixture.setUp()
        try:
            cfg,source,g,now=fixture.grant();cfg['actions']=[{'name':'propose_music_control','server':'configured','adapter':'music_control'}];g['allowed_actions']=['propose_music_control'];g['local_action_limits']={'music_control':2}
            p=fixture.folder/'normal.json';p.write_text(json.dumps(g))
            with patch('me3_assistant.guard.NORMAL',fixture.folder):
                with self.assertRaisesRegex(ValueError,'not_initialized'):authorize(cfg,source,p,fixture.folder,now)
                initialize_local_budget(g,fixture.folder/'local-action-budgets');authorize(cfg,source,p,fixture.folder,now)
        finally:fixture.tearDown()

class DecoderCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_stdout_is_drained_while_child_is_reaped(self):
        # Real child, deliberately enough output to pause the asyncio pipe reader.
        p=await asyncio.create_subprocess_exec(sys.executable,'-c','import os,time; os.write(1,b"x"*2000000); time.sleep(30)',stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
        try:
            async with asyncio.timeout(3):
                while not p.stdout._paused:await asyncio.sleep(.01)
            await asyncio.wait_for(reap_decoder(p),5)
            self.assertIsNotNone(p.returncode)
            self.assertTrue(p.stdout.at_eof())
        finally:
            if p.returncode is None:p.kill()
            await asyncio.wait_for(p.communicate(),5)

class MusicMCPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        (ROOT/'.tmp').mkdir(exist_ok=True);self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='music-mcp-');self.root=Path(self.tmp.name);self.media=self.root/'tracks';self.media.mkdir();tone(self.media/'Study.wav')
        self.before=hashlib.sha256((self.media/'Study.wav').read_bytes()).hexdigest();self.bus=MusicBus(self.root/'bus',True);self.collected=0;self.running=True
        async def consume():
            while self.running:
                if any(self.bus.mix(bytes(3840))):self.collected+=1
                await asyncio.sleep(.02)
        self.consumer=asyncio.create_task(consume());await asyncio.sleep(.03);self.events=Events(self.root/'events.jsonl')
        cfg={'version':1,'runtime':{'max_session_seconds':600,'max_turns':8,'tool_rounds':3,'batch_calls':3,'rpc_calls':100},'servers':[{'id':'music','module':'me3_assistant.music_server','args':['--music-root',str(self.media)],'identity':'me3','role':'utility','read_tools':['list_tracks','get_playback_status']}],'actions':[{'name':'propose_music_control','server':'music','adapter':'music_control'}]}
        p=self.root/'config.json';p.write_text(json.dumps(cfg));self.cfg=load(p)
    async def asyncTearDown(self):
        self.running=False;await self.consumer;self.bus.close(destroy=True);self.assertEqual(self.before,hashlib.sha256((self.media/'Study.wav').read_bytes()).hexdigest());self.tmp.cleanup()
    def setup_actions(self,r,limit=6):
        g=grant(limit);r.session_grant=g;r.local_action_directory=self.root/'local-actions';initialize_local_budget(g,r.local_action_directory)
        r.budget=FixtureBudget(0);r.journal=Journal(self.root/'journal.jsonl');r.allowed_actions=g['allowed_actions'];return Actions(r,r.config['actions'],r.budget,r.journal,self.events,lambda g:True,allowed=r.allowed_actions)
    async def test_discovery_real_catalog_status_and_private_endpoints_hidden(self):
        async with Registry(self.events,self.cfg,music_bus=self.bus.path) as r:
            self.assertEqual({d['name'] for d in r.declarations()},{'music__list_tracks','music__get_playback_status'})
            self.assertEqual((await r.read('music__list_tracks',1,{}))['tracks'][0]['track_id'],'Study.wav')
            self.assertEqual((await r.read('music__get_playback_status',1,{}))['state'],'stopped')
            with self.assertRaises(ToolFailure):await r.read('music__commit_music_control',1,{})
    @unittest.skipUnless(shutil.which('ffmpeg'),'FFmpeg not installed on this host')
    async def test_real_decoder_confirm_verify_pause_resume_volume_stop_and_idempotency(self):
        async with Registry(self.events,self.cfg,music_bus=self.bus.path) as r:
            a=self.setup_actions(r)
            for command,args in [('play',{'track_id':'Study.wav'}),('pause',{}),('volume',{'volume':10}),('resume',{}),('stop',{})]:
                prompt=await a.propose('propose_music_control',{'command':command,**args},1);self.assertIsNotNone(a.pending);pending=a.pending.copy()
                self.assertNotIn('software',prompt);reply=await a.confirm(history('Confirm',prompt),1)
                self.assertNotIn('could not',reply,command);self.assertEqual(r.journal.records[pending['operation_id']]['state'],'VERIFIED',command)
                duplicate=await r.rpc('music','commit_music_control',1,{'operation_id':pending['operation_id'],'token':pending['proposal']['token'],'confirmation':'confirm'});self.assertTrue(duplicate['duplicate'])
            self.assertGreater(self.collected,0);self.assertEqual(r.budget.used,0);self.assertEqual(self.bus.snapshot()['state'],'stopped')
    async def test_canceled_proposal_and_missing_local_allowance_never_execute(self):
        async with Registry(self.events,self.cfg,music_bus=self.bus.path) as r:
            a=self.setup_actions(r);prompt=await a.propose('propose_music_control',{'command':'play','track_id':'Study.wav'},1);self.assertIn('confirm',prompt);a.cancel()
            self.assertIn('no pending',await a.confirm(history('Confirm',prompt),1));self.assertEqual(self.bus.snapshot()['state'],'stopped')
            r.session_grant={};b=Actions(r,r.config['actions'],r.budget,r.journal,self.events,lambda g:True,allowed=r.allowed_actions)
            self.assertIn('not authorized',await b.propose('propose_music_control',{'command':'play','track_id':'Study.wav'},1));self.assertEqual(self.bus.snapshot()['state'],'stopped')
    @unittest.skipUnless(shutil.which('ffmpeg'),'FFmpeg not installed on this host')
    async def test_router_actual_media_action_uses_schema_and_current_human_confirmation(self):
        async with Registry(self.events,self.cfg,music_bus=self.bus.path) as r:
            self.setup_actions(r);model=Model(decision('propose_music_control',{'command':'play','track_id':'Study.wav'}));router=Router(model,r,self.events)
            prompt=await router.answer(history('Play my study track.'));self.assertIn('confirm',prompt);self.assertEqual(self.bus.snapshot()['state'],'stopped')
            answer=await router.answer(history('Confirm',prompt));self.assertIn('Playback started',answer);self.assertGreater(self.collected,0)
        self.assertEqual(self.bus.snapshot()['state'],'stopped')
    async def test_lifecycle_endpoints_cannot_be_configured_as_reads(self):
        cfg=dict(self.cfg);cfg['servers']=[dict(self.cfg['servers'][0],read_tools=['commit_music_control'])]
        with self.assertRaises(ToolFailure):
            async with Registry(self.events,cfg,music_bus=self.bus.path):pass
    async def test_changed_playback_epoch_invalidates_prepared_action(self):
        async with Registry(self.events,self.cfg,music_bus=self.bus.path) as r:
            op=uuid.uuid4().hex;p=await r.rpc('music','prepare_music_control',1,{'command':'play','track_id':'Study.wav','operation_id':op});self.bus.control(state=0)
            with self.assertRaises(ToolFailure):await r.rpc('music','commit_music_control',1,{'operation_id':op,'token':p['token'],'confirmation':'confirm'})
            self.assertEqual(self.bus.snapshot()['state'],'stopped')
    @unittest.skipUnless(shutil.which('ffmpeg'),'FFmpeg not installed on this host')
    async def test_actual_MP3_file_decodes_through_real_MCP_and_cleanup(self):
        p=await asyncio.create_subprocess_exec(shutil.which('ffmpeg'),'-nostdin','-loglevel','error','-i',str(self.media/'Study.wav'),'-codec:a','libmp3lame',str(self.media/'Study.mp3'),stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
        self.assertEqual(await asyncio.wait_for(p.wait(),10),0)
        async with Registry(self.events,self.cfg,music_bus=self.bus.path) as r:
            a=self.setup_actions(r);prompt=await a.propose('propose_music_control',{'command':'play','track_id':'Study.mp3'},1)
            answer=await a.confirm(history('Confirm',prompt),1);self.assertIn('Playback started',answer);self.assertGreater(self.collected,0)
        self.assertEqual(self.bus.snapshot()['state'],'stopped')

if __name__=='__main__':unittest.main()
