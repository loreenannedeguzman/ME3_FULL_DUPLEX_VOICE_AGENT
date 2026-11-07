"""Pure staged or installed chunking checks; no providers/devices/event loop."""
import ast,asyncio,base64,hashlib,os,random,runpy,shutil,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(os.environ.get('ME3_PROJECT_ROOT','/opt/ME3Deployment'))
STAGED=Path(os.environ.get('ME3_F1_OVERLAY',str(ROOT)))/'onit_candidate/presentation.py'
# Pinned from the pre-F1 module with only the two chunking definitions removed.
# This stays meaningful after installation replaces the old canonical module.
NONCHUNK_AST_SHA256='cfbb77cc58cb15d828911ae5f0185008b429a3938d93daeb168edf4a5cb9bfe6'

def load(path):
    # Execute only the pure presentation functions and the actual pure existing
    # helper bodies, avoiding imports of audio/controller/provider modules.
    namespace={'re':__import__('re')}
    for module,names in [('me3_assistant/spoken_text.py',{'spoken_text'}),('me3_assistant/speech.py',{'response_parts'})]:
        tree=ast.parse((ROOT/module).read_text(encoding='utf-8'))
        nodes=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.Assign)) and
               (isinstance(n,ast.Assign) or n.name in names)]
        exec(compile(ast.Module(body=nodes,type_ignores=[]),str(ROOT/module),'exec'),namespace)
    namespace['notation']=namespace.pop('spoken_text')
    tree=ast.parse(path.read_text(encoding='utf-8'))
    body=[n for n in tree.body if not isinstance(n,(ast.Import,ast.ImportFrom))]
    exec(compile(ast.Module(body=body,type_ignores=[]),str(path),'exec'),namespace)
    return namespace

NEW=load(STAGED)

def legacy_speech_parts(text):
    # Exact previous speech_parts body, using the actual pure response_parts.
    return [part[start:start+160] for part in NEW['response_parts'](text)
            for start in range(0,len(part),160)]

class SpeechPhrasing(unittest.TestCase):
    def parts(self,text):
        result=NEW['speech_parts'](text)
        self.assertEqual(''.join(result),text)
        self.assertTrue(all(0<len(p)<=160 for p in result))
        if result:self.assertLessEqual(len(result[0]),72)
        return result

    def test_only_chunking_definitions_change(self):
        new=ast.parse(STAGED.read_text(encoding='utf-8'))
        strip=lambda t:ast.dump(ast.Module(body=[n for n in t.body if not
          (isinstance(n,ast.FunctionDef) and n.name in {'speech_parts','_phrase_cut'})],type_ignores=[]))
        self.assertEqual(hashlib.sha256(strip(new).encode()).hexdigest(),NONCHUNK_AST_SHA256)

    def test_complete_initial_sentence_wins_over_comma(self):
        text='First, consider the clear explanation here, and hear the final result. '+('Further useful detail follows. '*10)
        parts=self.parts(text)
        self.assertTrue(parts[0].endswith('result.'))
        self.assertLessEqual(len(parts[0]),72)
        self.assertLess(len(legacy_speech_parts(text)[0]),len(parts[0]))

    def test_later_complete_sentence_wins_over_recent_comma(self):
        text='This initial sentence is complete and clear. '+('A useful sentence remains complete, although it has a comma. '*8)
        parts=self.parts(text)
        self.assertTrue(parts[1].rstrip().endswith('.'))
        self.assertTrue(all(not(p[-1].isalnum() and q[0].isalnum()) for p,q in zip(parts,parts[1:])))

    def test_initial_whitespace_fallback_remains_responsive(self):
        parts=self.parts(' '.join('ordinary' for _ in range(90)))
        self.assertLessEqual(len(parts[0]),72)
        self.assertGreaterEqual(len(parts[0]),35)

    def test_closing_quotes_remain_with_sentence(self):
        text='The answer is clear: "Please keep this complete sentence together." '+('Then continue carefully. '*10)
        parts=self.parts(text)
        self.assertTrue(parts[0].endswith('."'))

    def test_decimals_initials_and_list_markers_are_not_sentence_cuts(self):
        cut=NEW['_phrase_cut']
        for value in ['Temperature 3.5 stays stable without stopping', '1. '+('longword '*20), 'Ask Dr. '+('longword '*20),'Use e.g. '+('longword '*20)]:
            result=cut(value,72)
            self.assertNotIn(value[:result].rstrip(),{'1.','Ask Dr.','Use e.g.'})
        self.parts('Read A. B. and 3.5 carefully. '+('Continue with exact quantities. '*10))

    def test_numeric_grouping_is_identical(self):
        for count in range(8,21):
            text=', '.join(str(i*13) for i in range(count))+'.'
            self.assertEqual(self.parts(text),legacy_speech_parts(text))

    def test_long_tokens_and_mixed_token_edges_are_bounded_lossless(self):
        for text in ['x'*4000,'🙂'*801,'First sentence.\n'+'z'*900,'ab '*21+'W'*400+' final.', 'https://example.test/'+'x'*700]:
            self.parts(text)

    def test_exact_readbacks_code_and_path_projection_unchanged(self):
        values=[
          ('## AI tasks\n- **Decision**: call +12025550123.',
           'artificial intelligence tasks\nDecision: call plus six, three, nine, seven, six, two, seven, six, seven, eight, four, zero.'),
          ('`a_b ** AI` C:\\AI\\file_name.pdf https://example.org/AI?q=a_b',
           'a_b ** AI C:\\AI\\file_name.pdf https://example.org/AI?q=a_b'),
          ('```python\nx = 2 * 3 # AI\n```','x = 2 * 3 # AI\n'),
          ('Tell CONTACT_LABEL: Buy the cake before 5 p.m.; bring the documents.',
           'Tell CONTACT_LABEL: Buy the cake before 5 p.m.; bring the documents.')]
        for value,expected in values:
            projected=NEW['spoken_text'](value)
            self.assertEqual(projected,expected)
            self.parts(projected)

    def test_lossless_seeded_punctuation_and_unicode_edges(self):
        rng=random.Random(243)
        tokens=['word','3.14','Dr.','1.','example.py','🙂','café','"quoted."',';',':',',','\n','\t',' ']
        for _ in range(160):self.parts(' '.join(rng.choice(tokens) for _ in range(rng.randint(1,200))))

    def test_short_and_empty_shapes_unchanged(self):
        # response_parts('') is [''], but the legacy range(0,0,160) emitted no
        # synthesis chunks. Empty speech_parts is therefore [], not [''].
        self.assertEqual(NEW['speech_parts'](''),[])
        for value in ['', 'Yes.', 'Short answer.', 'Use API and PDF.']:
            self.assertEqual(NEW['speech_parts'](value),legacy_speech_parts(value))

    def test_installed_test_location_defaults_to_project_source(self):
        with tempfile.TemporaryDirectory(prefix='f1-installed-',dir=Path(__file__).parent) as folder:
            root=Path(folder)
            for relative,source in [('onit_candidate/presentation.py',STAGED),
              ('me3_assistant/speech.py',ROOT/'me3_assistant/speech.py'),
              ('me3_assistant/spoken_text.py',ROOT/'me3_assistant/spoken_text.py'),
              ('tests/test_speech_phrasing243.py',Path(__file__))]:
                target=root/relative;target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(source,target)
            environment={key:value for key,value in os.environ.items() if key!='ME3_F1_OVERLAY'}
            environment['ME3_PROJECT_ROOT']=str(root)
            with patch.dict(os.environ,environment,clear=True):
                installed=runpy.run_path(str(root/'tests/test_speech_phrasing243.py'))
            self.assertEqual(installed['STAGED'],root/'onit_candidate/presentation.py')
            # Run the two checks that formerly depended on the old root module.
            case=installed['SpeechPhrasing']()
            case.test_only_chunking_definitions_change()
            case.test_complete_initial_sentence_wins_over_comma()

# Real CandidateSpeech and asyncio Tasks, fake PCM/clients only. The Windows
# runner uses an I/O-rejecting selector: these fixtures have no sockets/devices,
# so an event-loop wakeup socket is unnecessary (and blocked by that sandbox).
class _NoIOSelector:
    def get_map(self):return {}
    def select(self,timeout=None):
        if timeout:time.sleep(min(timeout,.01))
        return []
    def close(self):pass
    def register(self,*args):raise AssertionError('fixture_IO_forbidden')
    def unregister(self,*args):raise AssertionError('fixture_IO_forbidden')

class _NoIOLoop(asyncio.SelectorEventLoop):
    def __init__(self):super().__init__(_NoIOSelector())
    def _make_self_pipe(self):pass
    def _close_self_pipe(self):pass
    def _write_to_self(self):pass

def run_audio_fixture(coro):
    with asyncio.Runner(loop_factory=_NoIOLoop if sys.platform=='win32' else None) as runner:
        return runner.run(asyncio.wait_for(coro,3))

class _Events:
    def __init__(self):self.rows=[]
    def emit(self,event,**fields):self.rows.append({'event':event,**fields})

class _Speech:
    def __init__(self,capacity=100,late=False):
        self.capacity=capacity;self.calls=[];self.late=late;self.entered=asyncio.Event()
        self.cancelled=False;self.active=0;self.maximum_active=0
    def require_tts_capacity(self,n):
        if self.capacity<n:raise ValueError('insufficient_complete_response_allowance')
    async def call(self,role,g,**args):
        if role!='tts':raise AssertionError('non_TTS_fixture_call')
        self.calls.append((g,args['text'],args['style']));self.capacity-=1
        self.active+=1;self.maximum_active=max(self.maximum_active,self.active)
        try:
            if self.late and len(self.calls)==2:
                self.entered.set()
                try:await asyncio.Event().wait()
                except asyncio.CancelledError:self.cancelled=True # Deliberately return late PCM.
            return {'pcm_b64':base64.b64encode(b'\x01\x00'*480).decode()}
        finally:self.active-=1

class CandidateSpeechFixtures(unittest.TestCase):
    TEXT='This initial sentence is complete and clear. '+('A useful sentence remains complete, although it has a comma. '*8)

    @classmethod
    def setUpClass(cls):
        # Import real controller dependencies outside the bounded async work.
        sys.path.insert(0,str(ROOT))
        from onit_candidate.spoken import CandidateSpeech
        cls.controller_type=CandidateSpeech

    def controller(self,speech,play):
        control=self.controller_type(speech,None,_Events(),play);control.clock.advance()
        return control

    def test_actual_controller_delivers_complete_projected_answer_once(self):
        async def check():
            speech=_Speech();played=[]
            async def play(pcm,g):played.append((g,pcm))
            c=self.controller(speech,play)
            try:
                with patch('onit_candidate.spoken.speech_parts',NEW['speech_parts']):
                    self.assertTrue(await c.speak(self.TEXT,1,'normal'))
                self.assertEqual(''.join(row[1] for row in speech.calls),self.TEXT)
                self.assertEqual(len(speech.calls),len(NEW['speech_parts'](self.TEXT)))
                self.assertEqual(len(played),len(speech.calls));self.assertEqual(speech.maximum_active,1)
                self.assertEqual(sum(r['event']=='response_complete' for r in c.events.rows),1)
                self.assertFalse(any(r.get('partial_output') for r in c.history))
            finally:await c.close()
        run_audio_fixture(check())

    def test_cancel_resistant_late_prefetch_cannot_play_obsolete_PCM(self):
        async def check():
            speech=_Speech(late=True);played=[]
            async def play(pcm,g):played.append(g)
            c=self.controller(speech,play)
            try:
                with patch('onit_candidate.spoken.speech_parts',NEW['speech_parts']):
                    c.spawn(c.speak(self.TEXT,1,'normal'));old=c.task
                    await asyncio.wait_for(speech.entered.wait(),1)
                    c.interrupt();await asyncio.gather(old,return_exceptions=True)
                self.assertEqual(played,[1]);self.assertTrue(speech.cancelled)
                self.assertEqual(len(speech.calls),2);self.assertEqual(speech.active,0)
                prefix=next(r for r in c.history if r.get('partial_output'))
                self.assertEqual(prefix['content'][0]['text'],NEW['speech_parts'](self.TEXT)[0])
                self.assertFalse(any(r['event']=='response_complete' for r in c.events.rows))
            finally:await c.close()
        run_audio_fixture(check())

    def test_complete_answer_capacity_failure_dispatches_no_TTS_or_PCM(self):
        async def check():
            speech=_Speech(capacity=1);played=[]
            async def play(pcm,g):played.append(g)
            c=self.controller(speech,play)
            try:
                with patch('onit_candidate.spoken.speech_parts',NEW['speech_parts']):
                    self.assertFalse(await c.speak(self.TEXT,1,'normal'))
                self.assertEqual(speech.calls,[]);self.assertEqual(played,[])
                self.assertTrue(any(r['event']=='turn_error' for r in c.events.rows))
            finally:await c.close()
        run_audio_fixture(check())

if __name__=='__main__':unittest.main(verbosity=2)
