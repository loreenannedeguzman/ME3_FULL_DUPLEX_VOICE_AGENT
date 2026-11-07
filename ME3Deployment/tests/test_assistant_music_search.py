"""Actual local bytes/MCP search; no inference, synthesis or hardware playback."""
import asyncio,json,os,shutil,subprocess,tempfile,unittest
from pathlib import Path
from me3_assistant.music import Catalog
from me3_assistant.music_catalog import syncsafe
from me3_assistant.registry import Registry,ToolFailure
from me3_assistant.config import load
from tests import test_assistant_music as f

def size(n):return bytes((n>>21&127,n>>14&127,n>>7&127,n&127))
def tagged(path,title='Only Time',artist='Énya',album='A Day Without Rain'):
    # Tag-reader fixture, deliberately not presented as a playable MP3.
    frames=[]
    for key,value in [(b'TIT2',title),(b'TPE1',artist),(b'TALB',album)]:
        body=b'\x03'+value.encode('utf-8');frames.append(key+size(len(body))+bytes(2)+body)
    data=b''.join(frames);path.write_bytes(b'ID3\x04\x00\x00'+size(len(data))+data+b'fixture-not-decoded')

class CatalogTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def test_actual_tags_accent_case_and_field_selection(self):
        tagged(self.root/'File01.mp3');tagged(self.root/'File02.mp3',title='Only Time Live',artist='Another Artist')
        c=Catalog(self.root);rows=c.search('ENYA','artist')['tracks'];self.assertEqual([x['track_id'] for x in rows],['File01.mp3']);self.assertEqual(rows[0]['artist'],'Énya');self.assertEqual(rows[0]['metadata_source'],['ID3v2'])
        self.assertEqual(c.search('only time')['matched_tracks'],2);self.assertEqual(c.search('rain','album')['matched_tracks'],2);self.assertEqual(c.search('unknown song')['tracks'],[])
    def test_filename_only_never_invents_artist_or_album(self):
        (self.root/'Local - Song.wav').write_bytes(b'not-decoded');row=Catalog(self.root).list()['tracks'][0]
        self.assertEqual(row['title'],'Local - Song');self.assertIsNone(row['artist']);self.assertIsNone(row['album']);self.assertFalse(row['artist_known']);self.assertEqual(Catalog(self.root).search('Local','artist')['tracks'],[])
    def test_bounded_page_unicode_serialization_and_full_catalog_count(self):
        for i in range(30):tagged(self.root/f'{i:02}.mp3',title='한'*96,artist='가'*96,album='나'*96)
        c=Catalog(self.root);offset=0;seen=[]
        while offset is not None:
            page=c.list(25,offset);self.assertEqual(page['total_tracks'],30);self.assertLess(len(json.dumps(page)),12000);seen.extend(x['track_id'] for x in page['tracks']);offset=page['next_offset']
        self.assertEqual(len(seen),30);self.assertEqual(len(set(seen)),30);self.assertEqual(len(c.inventory()['tracks']),30)
    def test_deleted_replaced_and_linked_files_are_not_opened_as_previous_identity(self):
        p=self.root/'one.mp3';tagged(p);c=Catalog(self.root);self.assertEqual(c.search('Enya')['matched_tracks'],1);tagged(p,artist='New Artist');self.assertEqual(c.search('Enya')['matched_tracks'],0)
        os.link(p,self.root/'two.mp3');self.assertEqual(c.list()['tracks'],[])
    def test_oversized_malformed_tags_and_unknown_encoding_stay_bounded(self):
        p=self.root/'huge.mp3';p.write_bytes(b'ID3\x04\x00\x00'+size(262145)+b'short');self.assertFalse(Catalog(self.root).list()['tracks'][0]['artist_known'])
        for raw in [b'\xff'*4,b'\x80\x00\x00\x01']:
            with self.assertRaises(ValueError):syncsafe(raw)
    def test_invalid_query_pagination_field_and_outside_paths(self):
        c=Catalog(self.root)
        for query in ['', ' '*3, 'a'*121,'line\nbreak',True]:
            with self.subTest(query=query),self.assertRaises(ValueError):c.search(query)
        for limit,offset in [(True,0),(0,0),(26,0),(1,-1),(1,True)]:
            with self.assertRaises(ValueError):c.list(limit,offset)
        with self.assertRaises(ValueError):c.search('x','path')
        with self.assertRaises(ValueError):c.open('../outside.mp3')
    def test_FLAC_comments_and_ID3v1_tags_are_actual_file_fields(self):
        comment=b'ARTIST=Forestella';data=(0).to_bytes(4,'little')+(1).to_bytes(4,'little')+len(comment).to_bytes(4,'little')+comment
        (self.root/'group.flac').write_bytes(b'fLaC'+bytes([0x84])+len(data).to_bytes(3,'big')+data)
        (self.root/'old.mp3').write_bytes(b'audio-placeholder'+b'TAG'+b'Old Title'.ljust(30,b'\0')+b'Old Artist'.ljust(30,b'\0')+b'Old Album'.ljust(30,b'\0')+bytes(35))
        self.assertEqual(Catalog(self.root).search('forestella','artist')['tracks'][0]['artist'],'Forestella');self.assertEqual(Catalog(self.root).search('Old Artist','artist')['tracks'][0]['metadata_source'],['ID3v1'])

class MCPSearchTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=f.MusicMCPTests.asyncSetUp
    asyncTearDown=f.MusicMCPTests.asyncTearDown
    async def test_actual_MCP_search_is_discovered_read_only_and_preserves_files_and_player(self):
        tagged(self.media/'Tagged.mp3');before=(self.media/'Tagged.mp3').read_bytes()
        cfg={**self.cfg,'servers':[{**self.cfg['servers'][0],'read_tools':['list_tracks','search_tracks','get_playback_status']}]}
        async with Registry(self.events,cfg,music_bus=self.bus.path) as r:
            result=await r.read('music__search_tracks',1,{'query':'enya','field':'artist'});self.assertEqual(result['matched_tracks'],1);self.assertEqual(result['tracks'][0]['track_id'],'Tagged.mp3');self.assertTrue(result['read_only'])
            self.assertEqual((await r.read('music__list_tracks',1,{'limit':1}))['total_tracks'],2)
            self.assertEqual((await r.read('music__get_playback_status',1,{}))['state'],'stopped')
            with self.assertRaises(ToolFailure):await r.read('music__commit_music_control',1,{})
        self.assertEqual(before,(self.media/'Tagged.mp3').read_bytes());self.assertEqual(self.bus.snapshot()['state'],'stopped')
    @unittest.skipUnless(shutil.which('ffmpeg'),'Real FFmpeg encoder unavailable')
    async def test_real_encoded_MP3_tags_roundtrip_through_actual_MCP(self):
        p=await asyncio.create_subprocess_exec(shutil.which('ffmpeg'),'-nostdin','-loglevel','error','-i',str(self.media/'Study.wav'),'-metadata','title=Study Session','-metadata','artist=Local Test Ensemble','-codec:a','libmp3lame',str(self.media/'Actual.mp3'),stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
        self.assertEqual(await asyncio.wait_for(p.wait(),10),0)
        cfg={**self.cfg,'servers':[{**self.cfg['servers'][0],'read_tools':['search_tracks']}]}
        async with Registry(self.events,cfg) as r:
            result=await r.read('music__search_tracks',1,{'query':'ensemble','field':'artist'});self.assertEqual(result['tracks'][0]['title'],'Study Session');self.assertEqual(result['tracks'][0]['track_id'],'Actual.mp3')

if __name__=='__main__':unittest.main()
