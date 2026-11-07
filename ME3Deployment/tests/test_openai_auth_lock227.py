"""Private synthetic fixtures only; no owner accounts, HTTP or inference.

PrivateChecks must run on POSIX without skips for deployment qualification.
"""
import asyncio,hashlib,json,os,sys,tempfile,threading,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from me3_openai import auth as a,transport as t

CID='oaiapp_lock_fixture';TOKEN='private-fixture-access-never-log'

def drive(coro):
    try:coro.send(None)
    except StopIteration as done:return done.value
    raise AssertionError('unexpected IO')

def snapshot(store):
    return {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in store.directory.iterdir() if p.is_file()}

class NoHTTP:
    def discovery(self):raise AssertionError('unexpected HTTP discovery')
    def post(self,*args):raise AssertionError('unexpected HTTP exchange')

class Ledger:
    def __init__(self):self.rows=[]
    def reserve(self,ordinal,model,**kwargs):self.rows.append(('reserve',ordinal));return ordinal
    def dispatch(self,ticket):raise AssertionError('must not dispatch after auth failure')
    def fail(self,ticket,code):self.rows.append(('fail',ticket,code))
class NoSession:
    def post(self,*args,**kwargs):raise AssertionError('must not call provider')

class LocalChecks(unittest.TestCase):
    def test_fixed_diagnostics_never_echo_arbitrary_auth_values(self):
        self.assertEqual(t.auth_failure_code(a.AuthError('auth_store_busy')),'auth_store_busy')
        for error in (a.AuthError(TOKEN),a.AuthError('auth_store_busy',TOKEN),ValueError('auth_store_busy'),a.AuthError({'token':TOKEN})):
            self.assertIsNone(t.auth_failure_code(error))
        self.assertEqual(a.AUTH_STORE_LOCK_TIMEOUT,1.0)

    def test_auth_failure_preserves_one_charge_without_dispatch_or_retry(self):
        for reason,expected in (('auth_store_busy','auth_store_busy'),(TOKEN,'openai_connection_failed_no_retry')):
            with self.subTest(reason_is_known=reason=='auth_store_busy'):
                ledger=Ledger();events=[];calls=[]
                def token():calls.append(1);raise a.AuthError(reason)
                tr=t.Transport(lambda name,**v:events.append((name,v)),get_token=token,ledger=ledger,account_sha256='a'*64,session=NoSession())
                tr.begin_goal('synthetic-lock-fixture');tr.bind_reservation(31)
                with self.assertRaises(t.TransportError) as caught:
                    drive(tr.request('reasoning',json=dict(model=t.MODEL,messages=[dict(role='user',content='Synthetic fixture.')],tools=[])))
                self.assertEqual(str(caught.exception),expected)
                self.assertEqual(ledger.rows,[('reserve',31),('fail',31,'transport_error')]);self.assertEqual(calls,[1])
                self.assertTrue(tr.failed);self.assertFalse(tr.active)
                self.assertEqual(events[-1][1]['error_code'],'auth_store_busy' if reason=='auth_store_busy' else 'request_failed_no_retry')
                self.assertFalse(events[-1][1]['dispatched']);self.assertNotIn(TOKEN,json.dumps(events))
                tr.end_goal()
                with self.assertRaises(t.TransportError):tr.begin_goal('no-automatic-retry')

    def test_non_POSIX_is_still_explicitly_refused(self):
        # Module-local proxy: changing the shared os.name would also change
        # pathlib's platform choice before AuthStore can exercise its guard.
        with patch.object(a,'os',SimpleNamespace(name='nt',open=os.open,supports_dir_fd=os.supports_dir_fd)):
            with self.assertRaisesRegex(a.AuthError,'private_storage_requires_POSIX'):a.AuthStore(ROOT)

@unittest.skipUnless(os.name=='posix','actual POSIX private-store qualification required')
class PrivateChecks(unittest.TestCase):
    def setUp(self):
        # Never relax permissions on an existing project/home/evidence folder.
        self.temp=tempfile.TemporaryDirectory(prefix='me3-auth227-',dir=Path.home());self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name);self.now=time.time();self.store=a.AuthStore(self.folder,clock=lambda:self.now)
        record=dict(client_id=CID,issuer=a.ISSUER,subject='synthetic-subject',ext_agent_host_id=self.store.host_id,identity_verified=True,access_token=TOKEN,id_token=None,refresh_token='private-fixture-refresh',token_type='Bearer',scopes=list(a.SCOPES),plan_enabled=True,saved_at=self.now,access_expires_at=self.now+3600)
        with self.store._locked() as fd:self.store._put(fd,self.store._name(CID),record)
        self.choice=self.store.save_selection(CID,t.MODEL,[t.MODEL])

    def launch(self,fn):
        result={};started=threading.Event()
        def work():
            started.set();begin=time.monotonic()
            try:result['value']=fn()
            except BaseException as exc:result['error']=exc
            finally:result['elapsed']=time.monotonic()-begin
        worker=threading.Thread(target=work,daemon=True);worker.start();self.assertTrue(started.wait(2));return worker,result

    def finish(self,worker,result):
        worker.join(4);self.assertFalse(worker.is_alive(),'private fixture worker must settle')
        if 'error' in result:raise result['error']
        return result['value']

    def test_same_store_short_thread_contention_waits_and_keeps_records(self):
        before=snapshot(self.store)
        with self.store._locked():
            worker,result=self.launch(self.store.selection);time.sleep(.12);self.assertTrue(worker.is_alive())
        self.assertEqual(self.finish(worker,result),self.choice)
        self.assertGreaterEqual(result['elapsed'],.10);self.assertLess(result['elapsed'],.8);self.assertEqual(snapshot(self.store),before)

    def test_separate_store_constructor_and_token_wait_for_file_lock(self):
        before=snapshot(self.store)
        def token():return t.Credentials(store=a.AuthStore(self.folder,clock=lambda:self.now),http=NoHTTP(),clock=lambda:self.now)._token()
        with self.store._locked():
            worker,result=self.launch(token);time.sleep(.12);self.assertTrue(worker.is_alive())
        self.assertEqual(self.finish(worker,result),TOKEN);self.assertGreaterEqual(result['elapsed'],.10)
        self.assertEqual(snapshot(self.store),before)

    def test_thread_and_file_timeouts_are_bounded_without_writes(self):
        other=a.AuthStore(self.folder);before=snapshot(self.store)
        for operation in (self.store.selection,other.selection):
            with self.store._locked():
                begin=time.monotonic()
                with self.assertRaisesRegex(a.AuthError,'^auth_store_busy$'):operation()
                elapsed=time.monotonic()-begin
            self.assertGreaterEqual(elapsed,.90);self.assertLess(elapsed,1.5)
            self.assertEqual(snapshot(self.store),before)
        self.assertEqual(other.selection(),self.choice)

    def test_thread_plus_file_contention_share_one_deadline(self):
        other=a.AuthStore(self.folder);before=snapshot(self.store)
        with other._locked():
            self.store._thread.acquire()
            timer=threading.Timer(.45,self.store._thread.release);timer.start()
            try:
                begin=time.monotonic()
                with self.assertRaisesRegex(a.AuthError,'^auth_store_busy$'):self.store.selection()
                elapsed=time.monotonic()-begin
            finally:timer.join()
        self.assertGreaterEqual(elapsed,.90);self.assertLess(elapsed,1.35,'not separate 1-second waits')
        self.assertEqual(self.store.selection(),self.choice);self.assertEqual(snapshot(self.store),before)

    def test_refresh_retains_lock_poll_times_out_and_follower_does_not_refresh_again(self):
        with self.store._locked() as fd:
            row=self.store._record(fd,CID);row['access_expires_at']=self.now+20;self.store._put(fd,self.store._name(CID),row)
        entered=threading.Event();release=threading.Event();calls=[]
        class HTTP:
            def discovery(inner):return {'keys':[]}
            def post(inner,url,form):
                calls.append((url,dict(form)));entered.set()
                if not release.wait(4):raise AssertionError('fixture release missing')
                return dict(access_token='rotated-fixture-access',refresh_token='rotated-fixture-refresh',token_type='Bearer',expires_in=3600)
        first=t.Credentials(store=self.store,http=HTTP(),clock=lambda:self.now)
        follower=t.Credentials(store=a.AuthStore(self.folder,clock=lambda:self.now),http=HTTP(),clock=lambda:self.now)
        worker,result=self.launch(first._token);self.assertTrue(entered.wait(2));begin=time.monotonic()
        try:
            with self.assertRaisesRegex(a.AuthError,'^auth_store_busy$'):a.AuthStore(self.folder)
            self.assertLess(time.monotonic()-begin,1.5);self.assertTrue(worker.is_alive());self.assertEqual(len(calls),1)
        finally:release.set()
        self.assertEqual(self.finish(worker,result),'rotated-fixture-access')
        self.assertEqual(follower._token(),'rotated-fixture-access');self.assertEqual(len(calls),1)
        self.assertEqual(self.store.selection(),self.choice)
        self.assertEqual(calls[0][0],a.TOKEN_ENDPOINT);self.assertEqual(calls[0][1]['grant_type'],'refresh_token')

    def test_parallel_polling_and_tokens_leave_exact_private_records_unchanged(self):
        before=snapshot(self.store)
        def poll():
            for _ in range(10):
                value=t.Credentials(store=a.AuthStore(self.folder,clock=lambda:self.now),http=NoHTTP(),clock=lambda:self.now)
                self.assertFalse(value.local_status()['refresh_required'])
        def tokens():
            value=t.Credentials(store=self.store,http=NoHTTP(),clock=lambda:self.now)
            for _ in range(10):self.assertEqual(value._token(),TOKEN)
        workers=[self.launch(fn) for fn in (poll,poll,tokens)]
        for worker,result in workers:self.finish(worker,result)
        self.assertEqual(snapshot(self.store),before)

    def test_permissions_symlinks_and_hardlinks_still_fail_closed(self):
        account=self.store.directory/self.store._name(CID);lock=self.store.directory/'session.lock'
        for path,operation in ((account,self.store.accounts),(lock,self.store.selection)):
            path.chmod(0o644)
            try:self.assertRaisesRegex(a.AuthError,'private_storage_permissions_or_links',operation)
            finally:path.chmod(0o600)
            extra=self.folder/'extra';os.link(path,extra)
            try:self.assertRaisesRegex(a.AuthError,'private_storage_permissions_or_links',operation)
            finally:extra.unlink()
            saved=self.folder/'saved';path.rename(saved);path.symlink_to(saved)
            try:self.assertRaises(a.AuthError,operation)
            finally:path.unlink();saved.rename(path)
        self.assertEqual(self.store.access_token(CID),TOKEN)

if __name__=='__main__':unittest.main(verbosity=2)
