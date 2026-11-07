"""Local SQLite/action/accounting contracts; no providers/accounts/audio."""
import asyncio,json,os,sqlite3,tempfile,unittest,uuid
from pathlib import Path
from types import SimpleNamespace as NS
from me3_productivity.store import Store,digest,instant,READ_SOURCE
from me3_productivity.action import ProductivityControl,ProductivityClarification
from me3_productivity.budget import initialize,StandingProductivityBudget,authorized
from me3_productivity.server import make_server,READ_TOOLS,PRIVATE_TOOLS

def uid():return uuid.uuid4().hex
HERE=Path(__file__).resolve().parent

class StoreTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(dir=HERE);self.root=Path(self.temp.name);self.clock=[1800000000.0]
  self.s=Store(self.root,clock=lambda:self.clock[0]);self.s.initialize();self.owner=uid()
 def tearDown(self):self.temp.cleanup()
 def prepare(self,kind='task',command='create',data=None,target=None,rev=None):return self.s.prepare(uid(),self.owner,kind,command,data if data is not None else {'title':'Example task'},target,rev)
 def commit(self,p):return self.s.commit(p['operation_id'],self.owner,p['token'],'confirm')
 def create(self,kind='task',data=None):
  p=self.prepare(kind,data=data);self.commit(p);return self.s.read(p['operation_id'],kind)['row']
 def test_prepare_has_no_record_effect_and_wrong_confirmation_never_commits(self):
  p=self.prepare();self.assertEqual(self.s.listing('task',status='all')['total'],0)
  for confirmation in ('yes','',None):
   with self.assertRaises(ValueError):self.s.commit(p['operation_id'],self.owner,p['token'],confirmation)
  self.assertEqual(self.s.listing('task',status='all')['total'],0)
 def test_create_restart_exact_readback_duplicate_commit_never_increments_revision(self):
  p=self.prepare(data={'title':'Study','body':'Exact\n  indented notes  \n','due_date':'2026-11-04'})
  result=self.commit(p);new=Store(self.root,clock=lambda:self.clock[0]);new.initialize()
  row=new.read(result['target_id'])['row'];self.assertEqual(row['body'],'Exact\n  indented notes  \n');self.assertEqual(row['revision'],1)
  self.assertTrue(new.commit(p['operation_id'],self.owner,p['token'],'confirm')['duplicate'])
  with self.assertRaisesRegex(ValueError,'operation_owner_mismatch'):new.commit(p['operation_id'],uid(),p['token'],'confirm')
  self.assertEqual(new.read(result['target_id'])['row']['revision'],1)
  proof=new.verify(p['operation_id']);self.assertEqual(proof['expected_digest'],p['expected_digest']);self.assertEqual(proof['applied_after_digest'],digest(p['intent']['after']));self.assertEqual(proof['verification_source'],READ_SOURCE)
 def test_optimistic_revision_blocks_stale_second_edit_atomically(self):
  row=self.create();a=self.prepare(command='update',data={'title':'First edit'},target=row['id'],rev=1);b=self.prepare(command='update',data={'title':'Stale edit'},target=row['id'],rev=1)
  self.commit(a)
  with self.assertRaisesRegex(ValueError,'stale_productivity_revision'):self.commit(b)
  self.assertEqual(self.s.read(row['id'])['row']['title'],'First edit');self.assertIsNone(self.s.verify(b['operation_id'])['row'])
 def test_complete_reopen_cancel_preserve_identity_and_audit(self):
  row=self.create();identifier=row['id']
  for revision,command,state in [(1,'complete','completed'),(2,'reopen','open'),(3,'cancel','canceled')]:
   p=self.prepare(command=command,data={},target=identifier,rev=revision);self.commit(p)
   self.assertEqual(self.s.read(identifier)['row']['state'],state)
  with self.assertRaises(ValueError):self.prepare(command='update',data={'title':'Should fail'},target=identifier,rev=4)
  self.assertEqual(self.s.listing('task',status='all')['total'],1)
 def test_exact_task_due_modes_clear_without_invented_clock(self):
  row=self.create(data={'title':'Due someday','due_date':'2026-11-03'})
  self.assertEqual(row['due_date'],'2026-11-03');self.assertIsNone(row['due_at'])
  p=self.prepare(command='update',data={'due_at':'2026-11-04T08:30:00+08:00'},target=row['id'],rev=1);self.commit(p)
  row=self.s.read(row['id'])['row'];self.assertIsNone(row['due_date']);self.assertEqual(row['due_at'],'2026-11-04T08:30:00+08:00')
  p=self.prepare(command='update',data={'clear_due':True},target=row['id'],rev=2);self.commit(p)
  self.assertIsNone(self.s.read(row['id'])['row']['due_at'])
 def test_missing_naive_mismatched_and_reversed_event_times_rejected(self):
  good=dict(title='Meeting',start_at='2026-11-05T09:00:00+08:00',end_at='2026-11-05T10:00:00+08:00',timezone='Asia/Manila')
  changes=[{'start_at':'2026-11-05T09:00:00'},{'timezone':'UTC'},{'end_at':'2026-11-05T08:00:00+08:00'},{'end_at':None}]
  for patch in changes:
   with self.assertRaises(ValueError):self.prepare('event',data={**good,**patch})
  self.assertEqual(self.s.listing('event',start_at='2026-11-05T00:00:00+08:00',end_at='2026-11-06T00:00:00+08:00')['total'],0)
 def test_DST_gap_rejected_and_both_explicit_fold_offsets_accepted(self):
  with self.assertRaises(ValueError):instant('2026-03-08T02:30:00-05:00','America/New_York')
  early=instant('2026-11-01T01:30:00-04:00','America/New_York')[1]
  late=instant('2026-11-01T01:30:00-05:00','America/New_York')[1]
  self.assertEqual(late-early,3600)
 def test_local_event_update_cancel_and_overlap_are_persistent(self):
  row=self.create('event',dict(title='Seminar',start_at='2026-11-05T09:00:00+08:00',end_at='2026-11-05T10:00:00+08:00'))
  page=self.s.listing('event',start_at='2026-11-05T09:30:00+08:00',end_at='2026-11-05T11:00:00+08:00');self.assertEqual(page['events'][0]['id'],row['id'])
  p=self.prepare('event','cancel',{},row['id'],1);self.commit(p)
  self.assertEqual(self.s.listing('event',start_at='2026-11-05T00:00:00+08:00',end_at='2026-11-06T00:00:00+08:00')['total'],0)
  self.assertEqual(Store(self.root).read(row['id'],'event')['row']['state'],'canceled')
 def test_note_literal_search_chunks_updates_and_archive(self):
  body='  exact\n'+('abc '*300)+'tail  '
  row=self.create('note',dict(title='100%_literal',body=body))
  self.create('note',dict(title='Other',body='different'))
  page=self.s.listing('note',query='%_');self.assertEqual(page['total'],1);self.assertNotIn('body',page['notes'][0]);self.assertIn('body_excerpt',page['notes'][0])
  first=self.s.read(row['id'],'note',max_body_chars=800);second=self.s.read(row['id'],'note',body_offset=first['next_body_offset'],expected_revision=1)
  self.assertEqual(first['row']['body']+second['row']['body'],body);self.assertTrue(second['body_complete'])
  p=self.prepare('note','archive',{},row['id'],1);self.commit(p)
  self.assertEqual(self.s.listing('note')['total'],1);self.assertEqual(self.s.listing('note',include_terminal=True)['total'],2)
 def test_body_continuation_requires_same_revision(self):
  row=self.create('note',dict(title='Versioned note',body='x'*1000));first=self.s.read(row['id'],'note')
  self.assertEqual(first['expected_revision'],1)
  with self.assertRaisesRegex(ValueError,'continuation_requires_expected_revision'):self.s.read(row['id'],'note',body_offset=800)
  with self.assertRaisesRegex(ValueError,'body_offset_out_of_range'):self.s.read(row['id'],'note',body_offset=1001,expected_revision=1)
  p=self.prepare('note','update',dict(body='y'*1000),row['id'],1);self.commit(p)
  with self.assertRaisesRegex(ValueError,'record_changed_read_fresh_version'):self.s.read(row['id'],'note',body_offset=800,expected_revision=first['row']['revision'])
 def test_later_edit_does_not_destroy_old_applied_operation_proof(self):
  old=self.prepare();self.commit(old);target=old['operation_id']
  later=self.prepare(command='update',data={'title':'Newer'},target=target,rev=1);self.commit(later)
  proof=self.s.verify(old['operation_id']);self.assertTrue(proof['superseded']);self.assertEqual(proof['applied_revision'],1);self.assertEqual(proof['current_revision'],2)
 def test_expired_foreign_and_changed_proposals_never_apply(self):
  p=self.prepare()
  with self.assertRaises(ValueError):self.s.commit(p['operation_id'],uid(),p['token'],'confirm')
  with self.assertRaises(ValueError):self.s.prepare(p['operation_id'],self.owner,'task','create',{'title':'Changed'})
  self.clock[0]+=121
  with self.assertRaises(ValueError):self.commit(p)
  self.assertEqual(self.s.listing('task',status='all')['total'],0)
 def test_noop_and_missing_revision_rejected(self):
  row=self.create()
  for rev in (None,True,0,2):
   with self.assertRaises(ValueError):self.prepare(command='update',data={'title':'Edit'},target=row['id'],rev=rev)
  with self.assertRaises(ValueError):self.prepare(command='update',data={'title':row['title']},target=row['id'],rev=1)
 def test_transport_bounds_pagination_and_clock_context(self):
  for i in range(5):self.create('note',dict(title='Note '+str(i),body='x'*1200))
  page=self.s.listing('note',limit=2);self.assertEqual(len(page['notes']),2);self.assertEqual(page['next_offset'],2)
  self.assertEqual(self.s.context()['default_timezone'],'Asia/Manila')
  with self.assertRaises(ValueError):self.prepare('note',data={'title':'Too long','body':'x'*6001})
 def test_hardlink_and_outside_root_store_rejected(self):
  with self.assertRaises(ValueError):Store(self.root,self.root.parent/'outside.sqlite3')
  link=self.root/'data/alias.sqlite3'
  try:os.link(self.s.path,link)
  except OSError:self.skipTest('hardlinks not available')
  with self.assertRaises(ValueError):self.s.context()
  link.unlink()

class BudgetTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(dir=HERE);self.path=Path(self.temp.name)/'counter.json';self.clock=[1000.0]
  self.grant=dict(profile='standing',mode='normal',routine_productivity=True,productivity_scope='pi_local_tasks_events_notes',routine_local_reminders=True,allowed_actions=['propose_productivity','propose_reminder'])
  self.old=[];self.base=NS(permitted=lambda role:True,reserve=lambda role:self.old.append(role))
  initialize(self.path);self.b=StandingProductivityBudget(self.base,self.grant,self.path,clock=lambda:self.clock[0])
 def tearDown(self):self.temp.cleanup()
 def test_separate_monotonic_roles_and_reinitialize_never_resets(self):
  self.assertEqual(self.b.reserve('productivity_control'),1);self.assertEqual(self.b.reserve('reminder_control'),1)
  self.assertEqual(initialize(self.path)['productivity_control'],1);self.assertEqual(self.old,[])
  self.b.reserve('reasoning');self.assertEqual(self.old,['reasoning'])
 def test_rate_limit_is_not_a_lifetime_allowance(self):
  for _ in range(30):self.b.reserve('productivity_control')
  self.assertFalse(self.b.permitted('reminder_control'))
  with self.assertRaises(ValueError):self.b.reserve('reminder_control')
  self.clock[0]+=61;self.assertTrue(self.b.permitted('productivity_control'));self.assertEqual(self.b.reserve('productivity_control'),31)
 def test_disabled_roles_and_counter_or_clock_rollback_fail(self):
  self.assertFalse(authorized({}));self.b.reserve('productivity_control')
  value=json.loads(self.path.read_bytes());value['productivity_control']=0;self.path.write_text(json.dumps(value))
  with self.assertRaises(ValueError):self.b.permitted('productivity_control')
 def test_private_permissions_survive_atomic_reservation(self):
  self.b.reserve('productivity_control');self.b.reserve('productivity_control')
  if os.name!='nt':self.assertEqual(self.path.stat().st_mode&0o777,0o600)

class AdapterTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.temp=tempfile.TemporaryDirectory(dir=HERE);self.root=Path(self.temp.name);self.store=Store(self.root);self.store.initialize();self.owner=uid();self.calls=[]
  self.registry=NS(schemas={('productivity',n):object() for n in PRIVATE_TOOLS},sessions={'productivity':(None,{'read_tools':READ_TOOLS})},communication_context=dict(generation=1,request='Save this local task.'),calls=0)
  async def rpc(server,tool,g,args):
   self.calls.append(tool);self.registry.calls+=1
   if tool=='prepare_productivity_change':
    data={k:args[k] for k in ('title','body') if k in args};return self.store.prepare(args['operation_id'],self.owner,args['kind'],args['command'],data,args.get('target_id'),args.get('expected_revision'))
   if tool=='commit_productivity_change':return self.store.commit(args['operation_id'],self.owner,args['token'],args['confirmation'])
   return self.store.verify(args['operation_id'])
  self.registry.rpc=rpc;self.adapter=ProductivityControl(self.registry,dict(name='propose_productivity',server='productivity'))
 async def asyncTearDown(self):self.temp.cleanup()
 async def test_real_adapter_prepare_commit_fresh_verification_and_exact_review(self):
  op=uid();proposal,expected,review=await self.adapter.prepare(dict(kind='task',command='create',title='Read the chapter',body='Pages 1 to 10'),1,op)
  self.assertIn('Pages 1 to 10',review);self.assertEqual(self.store.listing('task',status='all')['total'],0)
  pending=dict(operation_id=op,proposal=proposal,expected_digest=expected)
  await self.adapter.execute(pending,1);self.assertTrue(await self.adapter.verify(pending,1));self.assertIn('created your task "Read the chapter"',self.adapter.success_message(pending));self.assertNotIn('revision 1',self.adapter.success_message(pending))
  self.assertEqual(self.calls,['prepare_productivity_change','commit_productivity_change','read_productivity_operation'])
 async def test_invalid_input_is_clarified_before_any_private_RPC(self):
  with self.assertRaises(ProductivityClarification):await self.adapter.prepare(dict(kind='event',command='create',title='Ambiguous',start_at='tomorrow morning'),1,uid())
  self.assertEqual(self.calls,[])
 async def test_actual_MCP_catalogue_keeps_mutations_private(self):
  tools={t.name:t for t in await make_server(self.store).list_tools()}
  self.assertEqual(set(tools),set(READ_TOOLS+PRIVATE_TOOLS));self.assertNotIn('commit_productivity_change',READ_TOOLS)
  self.assertIn('expected_revision',tools['prepare_productivity_change'].inputSchema['properties'])
  self.assertEqual(tools['read_note'].inputSchema['properties']['delivery']['enum'],['reference','verbatim'])

if __name__=='__main__':unittest.main()
