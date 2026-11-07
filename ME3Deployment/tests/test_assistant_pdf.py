import asyncio,hashlib,json,os,subprocess,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from me3_assistant.library import Library,LibraryError
from me3_assistant import pdf_reader
from me3_assistant.pdf_reader import PDFError
from task_mcp.client import ROOT

def fixture_pdf(texts):
    # Disposable authored byte fixture, never a user deliverable or retained book.
    objects=[b'<< /Type /Catalog /Pages 2 0 R >>',b'']
    kids=[]
    for text in texts:
        page=len(objects)+1;content=page+1;kids.append(f'{page} 0 R')
        escaped=text.replace('\\','\\\\').replace('(','\\(').replace(')','\\)')
        stream=('BT /F1 12 Tf 72 720 Td ('+escaped+') Tj ET').encode('ascii')
        objects.extend([f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 {3+2*len(texts)} 0 R >> >> /Contents {content} 0 R >>'.encode(),b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'\nendstream'])
    objects[1]=('<< /Type /Pages /Kids ['+' '.join(kids)+'] /Count '+str(len(texts))+' >>').encode()
    objects.append(b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>')
    raw=b'%PDF-1.4\n';positions=[0]
    for i,obj in enumerate(objects,1):positions.append(len(raw));raw+=str(i).encode()+b' 0 obj\n'+obj+b'\nendobj\n'
    start=len(raw);raw+=f'xref\n0 {len(objects)+1}\n0000000000 65535 f \n'.encode()
    raw+=b''.join(f'{p:010d} 00000 n \n'.encode() for p in positions[1:])
    return raw+f'trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n'.encode()

class PDFBoundsTests(unittest.TestCase):
    def setUp(self):
        (ROOT/'.tmp').mkdir(exist_ok=True);self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='pdf-unit-');self.folder=Path(self.tmp.name);self.lib=Library(self.folder)
    def tearDown(self):self.tmp.cleanup()
    def test_catalog_uses_separate_PDF_size_bound_without_loading_contents(self):
        (self.folder/'Book.pdf').write_bytes(b'%PDF-'+bytes(200000));(self.folder/'Note.txt').write_text('note',encoding='utf-8')
        self.assertEqual([d['document_id'] for d in self.lib.list()['documents']],['Book.pdf','Note.txt'])
        with (self.folder/'Huge.pdf').open('wb') as f:f.truncate(pdf_reader.MAX_PDF_BYTES+1)
        with self.assertRaisesRegex(LibraryError,'pdf_size_limit'):self.lib.list()
    def test_pdf_search_is_truthfully_excluded_not_a_false_no_match(self):
        (self.folder/'Book.pdf').write_bytes(b'%PDF-needle');(self.folder/'Note.txt').write_text('needle here',encoding='utf-8')
        with patch('me3_assistant.pdf_reader.read_page') as reader:result=self.lib.search('needle');reader.assert_not_called()
        self.assertEqual(result['pdf_documents_not_searched'],1);self.assertEqual(result['matches'][0]['document_id'],'Note.txt')
    def test_page_offsets_versions_and_eof_are_explicit(self):
        (self.folder/'Book.pdf').write_bytes(b'fixture');version='a'*64
        with patch('me3_assistant.pdf_reader.read_page',return_value=('First page.',version,2)) as reader:
            first=self.lib.read('Book.pdf',max_chars=480);self.assertEqual(first['next_page'],2);self.assertEqual(first['next_offset'],0);self.assertFalse(first['eof']);self.assertTrue(first['page_eof'])
            last=self.lib.read('Book.pdf',expected_sha256=version,page=2);self.assertTrue(last['eof']);reader.assert_called_with(self.folder,'Book.pdf',2,version,None)
    def test_blank_pdf_page_is_not_a_fabricated_reading(self):
        (self.folder/'Scan.pdf').write_bytes(b'fixture')
        with patch('me3_assistant.pdf_reader.read_page',return_value=('', 'a'*64,2)):
            r=self.lib.read('Scan.pdf');self.assertFalse(r['page_has_extractable_text']);self.assertEqual(r['text'],'');self.assertEqual(r['next_page'],2)
    def test_bounds_and_missing_extractor_fail_closed(self):
        (self.folder/'Book.pdf').write_bytes(b'fixture');(self.folder/'Note.txt').write_text('note',encoding='utf-8')
        for args in ({'page':True},{'page':0},{'page':4097},{'offset':1},{'expected_sha256':'bad'}):
            with self.subTest(args=args),self.assertRaises(LibraryError):self.lib.read('Book.pdf',**args)
        with self.assertRaisesRegex(LibraryError,'page_only'):self.lib.read('Note.txt',page=2)
        with patch('me3_assistant.pdf_reader.available',return_value=False),self.assertRaisesRegex(LibraryError,'unavailable'):self.lib.read('Book.pdf')
    def test_child_timeout_kills_reaps_and_does_not_forward_credentials(self):
        class Process:
            calls=0;killed=False
            def wait(self,timeout):
                self.calls+=1
                if not self.killed:time.sleep(.002);raise subprocess.TimeoutExpired('fixture',timeout)
                return -9
            def kill(self):self.killed=True
            def poll(self):return -9 if self.killed else None
        proc=Process()
        with patch.dict(os.environ,{'GOOGLE_API_KEY':'fixture-secret'}),patch('me3_assistant.pdf_reader.subprocess.Popen',return_value=proc) as spawn,patch('me3_assistant.pdf_reader.time.monotonic',side_effect=[0.,0.,2.]):
            with self.assertRaisesRegex(PDFError,'timeout'):pdf_reader._run('text',3,1,1.)
            self.assertTrue(proc.killed);self.assertEqual(proc.calls,2);self.assertNotIn('GOOGLE_API_KEY',spawn.call_args.kwargs['env']);self.assertEqual(spawn.call_args.kwargs['pass_fds'],(3,))
    def test_child_output_is_bounded_even_if_a_worker_claims_success(self):
        class Process:
            def wait(self,timeout):return 0
        def spawn(*args,**kw):kw['stdout'].write(bytes(pdf_reader.MAX_OUTPUT+1));kw['stdout'].flush();return Process()
        with patch('me3_assistant.pdf_reader.subprocess.Popen',side_effect=spawn),self.assertRaisesRegex(PDFError,'text_limit'):pdf_reader._run('text',3,1,time.monotonic()+1)

@unittest.skipUnless(pdf_reader.available(),'Native PDF extraction requires Pi/Linux Poppler')
class NativePDFTests(unittest.TestCase):
    setUp=PDFBoundsTests.setUp
    tearDown=PDFBoundsTests.tearDown
    def test_real_two_page_PDF_has_versioned_bounded_passages(self):
        path=self.folder/'Book.pdf';raw=fixture_pdf(['The brass key is in the blue drawer.','Bring a notebook and a pencil.']);path.write_bytes(raw)
        first=self.lib.read('Book.pdf',max_chars=480);self.assertIn('blue drawer',first['text']);self.assertEqual(first['sha256'],hashlib.sha256(raw).hexdigest());self.assertEqual(first['page_count'],2)
        second=self.lib.read('Book.pdf',page=first['next_page'],expected_sha256=first['sha256']);self.assertIn('notebook',second['text']);self.assertTrue(second['eof'])
        path.write_bytes(raw+b'%changed\n')
        with self.assertRaisesRegex(LibraryError,'version_changed'):self.lib.read('Book.pdf',page=2,expected_sha256=first['sha256'])
    def test_native_bad_page_malformed_file_and_links_are_rejected(self):
        path=self.folder/'Book.pdf';path.write_bytes(fixture_pdf(['One page.']))
        with self.assertRaisesRegex(LibraryError,'page_outside'):self.lib.read('Book.pdf',page=2)
        (self.folder/'Broken.pdf').write_bytes(b'not a pdf')
        with self.assertRaisesRegex(LibraryError,'extraction_failed'):self.lib.read('Broken.pdf')
        (self.folder/'Link.pdf').symlink_to(path)
        with self.assertRaisesRegex(LibraryError,'link_or_type'):self.lib.read('Link.pdf')
    def test_changed_file_during_native_read_is_rejected(self):
        path=self.folder/'Book.pdf';path.write_bytes(fixture_pdf(['Original.']))
        def changed(role,*args):
            if role=='info':return 'Pages: 1\n'
            path.write_bytes(fixture_pdf(['Changed.']));return 'Original.'
        with patch('me3_assistant.pdf_reader._run',side_effect=changed),self.assertRaisesRegex(LibraryError,'document_changed'):self.lib.read('Book.pdf')

class PDFCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_sets_signal_and_waits_for_thread_cleanup(self):
        import threading
        from me3_assistant.library_server import cancellable_pdf_read
        started=threading.Event();finished=threading.Event()
        def operation(cancel):
            started.set()
            try:
                if not cancel.wait(2):raise AssertionError('cancel not propagated')
                return {'ok':False,'error':'pdf_read_cancelled'}
            finally:finished.set()
        task=asyncio.create_task(cancellable_pdf_read(operation))
        while not started.is_set():await asyncio.sleep(.001)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertTrue(finished.is_set())
    async def test_success_passes_result_without_changing_it(self):
        from me3_assistant.library_server import cancellable_pdf_read
        result=await cancellable_pdf_read(lambda cancel:{'ok':True,'text':'fixture'})
        self.assertEqual(result,{'ok':True,'text':'fixture'})

@unittest.skipUnless(pdf_reader.available(),'Native PDF MCP requires Pi/Linux Poppler')
class NativePDFMCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_MCP_lists_and_reads_versioned_PDF_pages_without_logging_text(self):
        from me3_assistant.config import load
        from me3_assistant.registry import Registry
        from tests.test_assistant_mode import Events
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='pdf-mcp-') as temp:
            folder=Path(temp);books=folder/'books';books.mkdir();raw=fixture_pdf(['The brass key is in the blue drawer.','Bring a notebook and a pencil.']);(books/'Book.pdf').write_bytes(raw)
            cfg={'version':1,'runtime':{'max_session_seconds':600,'max_turns':8,'tool_rounds':3,'batch_calls':3,'rpc_calls':32},'servers':[{'id':'library','module':'me3_assistant.library_server','args':['--library-root',str(books)],'identity':'me3','role':'utility','read_tools':['list_documents','search_documents','read_document']}],'actions':[]}
            path=folder/'config.json';path.write_text(json.dumps(cfg),encoding='utf-8');events=Events(folder/'events.jsonl')
            async with Registry(events,load(path)) as registry:
                listing=await registry.read('library__list_documents',1,{});self.assertEqual(listing['documents'][0]['document_id'],'Book.pdf')
                first=await registry.read('library__read_document',1,{'document_id':'Book.pdf','page':1});self.assertIn('blue drawer',first['text'])
                spoken=await registry.read('library__read_document',1,{'document_id':'Book.pdf','page':1,'delivery':'verbatim','max_words':3})
                self.assertEqual(len(spoken['text'].split()),3);self.assertFalse(spoken['page_eof']);self.assertNotIn('next_page',spoken)
                rest=await registry.read('library__read_document',1,{'document_id':'Book.pdf','page':1,'offset':spoken['next_offset'],'expected_sha256':spoken['sha256']})
                self.assertEqual(spoken['text']+rest['text'],first['text'])
                second=await registry.read('library__read_document',1,{'document_id':'Book.pdf','page':first['next_page'],'expected_sha256':first['sha256']});self.assertIn('notebook',second['text']);self.assertTrue(second['eof'])
            self.assertEqual((books/'Book.pdf').read_bytes(),raw);self.assertNotIn('blue drawer',json.dumps(events.rows))

if __name__=='__main__':unittest.main()
