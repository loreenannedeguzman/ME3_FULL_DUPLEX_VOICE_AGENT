"""Provider/account-free request ticket and append-only storage checks."""
import copy,importlib.util,json,os,stat,sys,tempfile,threading,unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('plan_usage',HERE.parent/'me3_openai/usage.py');u=importlib.util.module_from_spec(spec);sys.modules[spec.name]=u;spec.loader.exec_module(u)
class Memory(u.UsageLedger):
    def __init__(self,raw=b''):self.raw=raw;self.clock=lambda:1000.;self._thread=threading.Lock()
    @contextmanager
    def _locked(self):
        u.need(self._thread.acquire(blocking=False),'usage_ledger_busy')
        try:yield None
        finally:self._thread.release()
    def _raw(self,fd):return self.raw
    def _append(self,fd,line):self.raw+=line
def resigned(rows):
    result=[];previous=u.ZERO
    for i,row in enumerate(rows):
        row=copy.deepcopy(row);row.update(sequence=i+1,previous_sha256=previous);row.pop('event_sha256',None);row['event_sha256']=u.digest(row);previous=row['event_sha256'];result.append(u.canonical(row)+b'\n')
    return b''.join(result)
class Tickets(unittest.TestCase):
    def setUp(self):self.ledger=Memory()
    def reserve(self,ordinal=1):return self.ledger.reserve(ordinal,'gpt-fixture','qualification-205','a'*64)
    def test_actual_usage_no_artificial_output_cap(self):
        ticket=self.reserve(1001);first=self.ledger.raw;self.ledger.dispatch(ticket)
        self.ledger.complete(ticket,{'input_tokens':1000,'output_tokens':5000,'total_tokens':6000,'output_tokens_details':{'reasoning_tokens':400}},'resp_fixture')
        state=self.ledger.snapshot();self.assertTrue(self.ledger.raw.startswith(first));self.assertEqual(state['requests_reserved'],1);self.assertEqual(state['events'],3);self.assertEqual(state['request_refunds'],0);self.assertFalse(state['token_allowance_inferred'])
        self.assertEqual(state['tickets'][0]['usage'],{'input_tokens':1000,'output_tokens':5000,'total_tokens':6000});self.assertEqual(state['tickets'][0]['ordinal'],1001)
    def test_unknown_usage_stays_null(self):
        ticket=self.reserve();self.ledger.dispatch(ticket);self.ledger.interrupt(ticket)
        self.assertIsNone(self.ledger.snapshot()['tickets'][0]['usage']);self.assertEqual(self.ledger.snapshot()['requests_reserved'],1)
    def test_partial_usage_not_invented_or_summed(self):
        ticket=self.reserve();self.ledger.dispatch(ticket);self.ledger.complete(ticket,{'output_tokens':4000})
        self.assertEqual(self.ledger.snapshot()['tickets'][0]['usage'],{'input_tokens':None,'output_tokens':4000,'total_tokens':None})
    def test_failed_before_dispatch_preserves_reservation(self):
        ticket=self.reserve();self.ledger.fail(ticket,'source_changed');self.assertEqual([r['state'] for r in self.ledger.events()],['RESERVED','FAILED']);self.assertIsNone(self.ledger.events()[-1]['usage'])
    def test_once_only_dispatch_and_terminal(self):
        ticket=self.reserve();self.ledger.dispatch(ticket);old=self.ledger.raw;self.assertRaises(u.UsageError,self.ledger.dispatch,ticket);self.assertEqual(self.ledger.raw,old)
        self.ledger.fail(ticket,'http_429');old=self.ledger.raw
        for fn in (self.ledger.dispatch,self.ledger.complete,self.ledger.fail,self.ledger.interrupt):self.assertRaises(u.UsageError,fn,ticket)
        self.assertEqual(self.ledger.raw,old)
    def test_complete_requires_dispatch(self):
        ticket=self.reserve();old=self.ledger.raw;self.assertRaises(u.UsageError,self.ledger.complete,ticket);self.assertEqual(self.ledger.raw,old)
    def test_duplicate_ordinal_no_reuse_or_refund(self):
        ticket=self.reserve(42);self.ledger.fail(ticket);old=self.ledger.raw;self.assertRaises(u.UsageError,self.reserve,42);self.assertEqual(self.ledger.raw,old)
        self.reserve(44);self.reserve(43);self.assertEqual(self.ledger.snapshot()['requests_reserved'],3,'distinct real ordinals may arrive out of order; ledger is not budget authority')
    def test_restart_observes_dispatched_without_invented_terminal(self):
        ticket=self.reserve();self.ledger.dispatch(ticket);other=Memory(self.ledger.raw);self.assertEqual(other.snapshot()['tickets'][0]['state'],'DISPATCHED');self.assertIsNone(other.snapshot()['tickets'][0]['usage'])
        other.interrupt(ticket);self.assertEqual(other.snapshot()['tickets'][0]['state'],'INTERRUPTED');self.assertEqual(other.snapshot()['requests_reserved'],1)
    def test_unknown_ticket_and_bad_fields_do_not_append(self):
        self.assertRaises(u.UsageError,self.ledger.dispatch,'b'*32)
        for ordinal in (True,0,-1,'1'):
            self.assertRaises(u.UsageError,self.ledger.reserve,ordinal,'gpt-fixture')
        self.assertEqual(self.ledger.raw,b'')
    def test_unknown_usage_values_and_secret_like_reason_rejected(self):
        ticket=self.reserve();self.ledger.dispatch(ticket);old=self.ledger.raw
        for usage in ({'total_tokens':True},{'output_tokens':-1},{'input_tokens':1,'output_tokens':2,'total_tokens':5},{'total_tokens':float('nan')}):self.assertRaises(u.UsageError,self.ledger.complete,ticket,usage)
        self.assertRaises(u.UsageError,self.ledger.fail,ticket,'Bearer private credential');self.assertEqual(self.ledger.raw,old)
    def test_torn_chain_duplicate_and_regression_fail_closed(self):
        ticket=self.reserve();self.ledger.dispatch(ticket);rows=self.ledger.events()
        for raw in (self.ledger.raw[:-1],self.ledger.raw.replace(b'gpt-fixture',b'gpt-changed'),self.ledger.raw+b'{}\n'):
            self.assertRaises(u.UsageError,u.parse,raw)
        for mutate in (lambda r:r[-1].update(state='RESERVED'),lambda r:r[-1].update(model='another-model'),lambda r:r[-1].update(ordinal=2)):
            modified=copy.deepcopy(rows);mutate(modified);self.assertRaises(u.UsageError,u.parse,resigned(modified))
        self.ledger.complete(ticket);rows=self.ledger.events();self.assertRaises(u.UsageError,u.parse,resigned(rows+[rows[-1]]))
    def test_busy_ledger_never_appends(self):
        with self.ledger._locked():self.assertRaises(u.UsageError,self.reserve)
        self.assertEqual(self.ledger.raw,b'')
    def test_permission_predicate_hardlinks_foreign_owner_and_public(self):
        with patch.object(os,'geteuid',return_value=1000,create=True):
            valid={'st_mode':stat.S_IFREG|0o600,'st_uid':1000,'st_nlink':1};self.ledger._info(type('I',(),valid)())
            for changes in ({'st_mode':stat.S_IFREG|0o644},{'st_uid':1001},{'st_nlink':2},{'st_mode':stat.S_IFLNK|0o600}):
                fields=dict(valid,**changes);self.assertRaises(u.UsageError,self.ledger._info,type('I',(),fields)())

class Filesystem(unittest.TestCase):
    def test_actual_POSIX_append_and_shared_lock_or_fail_closed_Windows(self):
        if os.name!='posix':self.assertRaises(u.UsageError,u.UsageLedger,HERE);return
        with tempfile.TemporaryDirectory(prefix='me3-usage-',dir=Path.home()) as folder:
            data=Path(folder);private=data/'openai-plan';private.mkdir(mode=0o700)
            untouched=private/'account-fixture.json';untouched.write_bytes(b'private-auth-not-read');untouched.chmod(0o600)
            ledger=u.UsageLedger(data);other=u.UsageLedger(data);ticket=ledger.reserve(501,'gpt-fixture');journal=private/'subscription-usage.jsonl';prefix=journal.read_bytes()
            ledger.dispatch(ticket);self.assertTrue(journal.read_bytes().startswith(prefix));self.assertEqual(other.snapshot()['tickets'][0]['state'],'DISPATCHED')
            with ledger._locked():self.assertRaises(u.UsageError,other.dispatch,ticket)
            other.complete(ticket);self.assertEqual(ledger.snapshot()['tickets'][0]['state'],'COMPLETED');self.assertEqual(untouched.read_bytes(),b'private-auth-not-read')
            for path in (journal,private/'subscription-usage.lock'):self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o600)
            link=data/'linked-journal';os.link(journal,link);self.assertRaises(u.UsageError,ledger.snapshot);link.unlink()
            with journal.open('ab') as stream:stream.write(b'broken')
            old=journal.read_bytes();self.assertRaises(u.UsageError,ledger.reserve,502,'gpt-fixture');self.assertEqual(journal.read_bytes(),old)
    def test_actual_POSIX_no_symlink_or_fail_closed_Windows(self):
        if os.name!='posix':self.assertRaises(u.UsageError,u.UsageLedger,HERE);return
        with tempfile.TemporaryDirectory(prefix='me3-usage-link-',dir=Path.home()) as folder:
            data=Path(folder);private=data/'openai-plan';private.mkdir(mode=0o700)
            target=data/'other';target.write_bytes(b'outside');target.chmod(0o600);(private/'subscription-usage.jsonl').symlink_to(target)
            ledger=u.UsageLedger(data);self.assertRaises(u.UsageError,ledger.snapshot);self.assertEqual(target.read_bytes(),b'outside')

if __name__=='__main__':unittest.main(verbosity=2)
