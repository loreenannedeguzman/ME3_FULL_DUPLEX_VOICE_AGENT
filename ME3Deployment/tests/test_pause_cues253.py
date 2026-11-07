"""Actual endpoint/startup methods with inert clocks/speech; no audio/model."""
import ast,os,unittest
from pathlib import Path
from types import SimpleNamespace,MethodType

P=Path(__file__).resolve().parents[1]
ROOT=Path(os.environ.get('ME3_PROJECT_ROOT','/opt/ME3Deployment'));O=Path(os.environ.get('ME3_LATENCY_STAGE',ROOT))

def run(coro):
    try:coro.send(None)
    except StopIteration as result:return result.value
    finally:coro.close()
    raise AssertionError('Unexpected await; step explicitly')
class Hold:
    def __await__(self):yield 'bounded-hold';return None

class Fixture:
    def __init__(self,*,complete=False,pending=False,normal=False,explicit=None):
        self.calls=[];self.events=[];self.submitted=[];self.waits=[];self.greetings=[];self.live=True
        self.snapshot=b'\x01\x00'*4000
        async def sleep(seconds):self.waits.append(seconds);await Hold()
        async def endpoint(snapshot,token):self.calls.append(('endpoint',snapshot,token));return complete
        provider=SimpleNamespace(pending={'immutable':'review'} if pending else None)
        self.log=SimpleNamespace(emit=lambda name,**data:self.events.append((name,data)))
        namespace=dict(time=SimpleNamespace(monotonic=lambda:0),asyncio=SimpleNamespace(sleep=sleep),events=self.log,provider=provider,Ownership=SimpleNamespace(HOLD='hold',PROCESSING='processing'))
        tree=ast.parse((O/'me3_completion/pipeline.py').read_text())
        methods=[n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) and n.name in {'decide','commit'}]
        exec(compile(ast.Module(body=methods,type_ignores=[]),'<actual-staged-endpoint-methods>','exec'),namespace)
        self.p=SimpleNamespace(endpoint=SimpleNamespace(decide=endpoint),valid=lambda token:self.live and token==4,
            transition=lambda *x:None,cued=False,cue=None,active_end=32000,onset=0,pcm=bytearray(self.snapshot),preroll=bytearray(b'pre'),overflow=False,closing=False)
        self.p.controller=SimpleNamespace(clock=SimpleNamespace(current=0),submit=lambda pcm,g:self.submitted.append((pcm,g)),greet=lambda:self.greetings.append('actual-greeting'))
        self.p.decide=MethodType(namespace['decide'],self.p);self.p.commit=MethodType(namespace['commit'],self.p)
        async def cue(token):self.calls.append(('optional-tts',token))
        self.p.backchannel=cue
        def spawn(coro):run(coro);return SimpleNamespace(done=lambda:True)
        self.p.spawn=spawn
        if explicit is not None:self.p.allow_speculative_pause_cues=explicit
        policy_tree=ast.parse((O/'me3_assistant/voice_policy.py').read_text())
        install=next(n for n in policy_tree.body if isinstance(n,ast.FunctionDef) and n.name=='install_normal_startup')
        local={};exec(compile(ast.Module(body=[install],type_ignores=[]),'<actual-normal-startup>','exec'),local)
        self.install=local['install_normal_startup']
        if normal:self.install(self.p,self.log)

class PauseCues253(unittest.TestCase):
    def test_normal_startup_disables_speculation_before_start_without_priming(self):
        f=Fixture(normal=True)
        self.assertIs(f.p.allow_speculative_pause_cues,False)
        self.assertEqual(f.calls,[]);self.assertEqual(f.greetings,[])
        run(f.p.startup())
        self.assertEqual(f.greetings,['actual-greeting']);self.assertEqual(f.calls,[])
        self.assertEqual(f.events[0][1]['speculative_pause_cues'],False)
        self.assertEqual(f.events[0][1]['unused_cue_priming'],False)
    def test_incomplete_normal_wait_retains_exact_audio_then_commits_without_tts(self):
        f=Fixture(normal=True);coro=f.p.decide(4,f.snapshot)
        self.assertEqual(coro.send(None),'bounded-hold')
        self.assertEqual(f.waits,[2.3]);self.assertEqual(f.submitted,[])
        self.assertFalse(any(row[0]=='optional-tts' for row in f.calls))
        with self.assertRaises(StopIteration):coro.send(None)
        self.assertEqual(f.submitted,[(f.snapshot,0)]);self.assertEqual(f.p.pcm,bytearray())
        self.assertTrue(any(name=='completion_hold_expired' for name,_ in f.events))
    def test_owner_resumes_during_normal_hold_without_tts_or_stale_commit(self):
        f=Fixture(normal=True);coro=f.p.decide(4,f.snapshot);coro.send(None);f.live=False
        with self.assertRaises(StopIteration):coro.send(None)
        self.assertEqual(f.submitted,[]);self.assertFalse(any(row[0]=='optional-tts' for row in f.calls))
    def test_shared_legacy_default_preserves_optional_cue(self):
        f=Fixture();coro=f.p.decide(4,f.snapshot);coro.send(None)
        self.assertEqual([row for row in f.calls if row[0]=='optional-tts'],[('optional-tts',4)])
        with self.assertRaises(StopIteration):coro.send(None)
        self.assertEqual(f.submitted,[(f.snapshot,0)])
    def test_explicit_true_legacy_and_pending_review_guard_remain(self):
        for pending in (False,True):
            f=Fixture(pending=pending,explicit=True);coro=f.p.decide(4,f.snapshot);coro.send(None)
            self.assertEqual(len([row for row in f.calls if row[0]=='optional-tts']),0 if pending else 1)
            with self.assertRaises(StopIteration):coro.send(None)
    def test_complete_endpoint_needs_no_hold_or_speculative_speech(self):
        f=Fixture(normal=True,complete=True);run(f.p.decide(4,f.snapshot))
        self.assertEqual(f.waits,[]);self.assertEqual(f.submitted,[(f.snapshot,0)])
        self.assertFalse(any(row[0]=='optional-tts' for row in f.calls))
    def test_explicit_semantic_cue_ack_resume_and_pacing_methods_are_exact247(self):
        def methods(path):
            return {n.name:ast.dump(n,include_attributes=False) for n in ast.walk(ast.parse(path.read_text())) if isinstance(n,(ast.AsyncFunctionDef,ast.FunctionDef))}
        old=methods(ROOT/'me3_completion/pipeline.py');new=methods(O/'me3_completion/pipeline.py')
        for name in ('semantic_cue','backchannel','classify_overlap','play','commit','process_frame'):
            self.assertEqual(new[name],old[name],name)
    def test_normal_nonzero_generation_or_closing_never_regreets(self):
        for closing,generation in ((True,0),(False,2)):
            f=Fixture(normal=True);f.p.closing=closing;f.p.controller.clock.current=generation
            run(f.p.startup());self.assertEqual(f.greetings,[]);self.assertEqual(f.calls,[])

if __name__=='__main__':unittest.main(verbosity=2)
