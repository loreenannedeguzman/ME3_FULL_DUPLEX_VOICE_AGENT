"""Intent/confirmation regressions use real MCP and isolated SQLite only."""
import contextlib,sqlite3,unittest
from tests import test_assistant_mode as fixtures

class ConfirmationIntentTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixtures.MCPTests.asyncSetUp
    asyncTearDown=fixtures.MCPTests.asyncTearDown
    cfg=fixtures.MCPTests.cfg
    action_router=fixtures.MCPTests.action_router
    async def test_question_confirmation_does_not_commit_or_spend_write_reservation(self):
        async with self.action_router() as (router,r):
            prompt=await router.answer(fixtures.history('Please save three notebooks.'))
            reply=await router.answer(fixtures.history('Confirm?',prompt))
            self.assertEqual(r.budget.used,0)
            self.assertEqual(reply,prompt)
            self.assertIsNotNone(router.pending)
            with contextlib.closing(sqlite3.connect(self.folder/'test.sqlite3')) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM items').fetchone()[0],0)
    async def test_inverted_question_confirmation_is_not_a_replacement_or_write(self):
        async with self.action_router() as (router,r):
            prompt=await router.answer(fixtures.history('Please save three notebooks.'))
            reply=await router.answer(fixtures.history('¿Confirm?',prompt))
            self.assertEqual(reply,prompt);self.assertIsNotNone(router.pending)
            self.assertEqual(r.budget.used,0)
    async def test_clear_confirmation_after_uncertain_question_commits_once(self):
        async with self.action_router() as (router,r):
            prompt=await router.answer(fixtures.history('Please save three notebooks.'))
            repeated=await router.answer(fixtures.history('Confirm?',prompt))
            reply=await router.answer(fixtures.history('Confirm.',repeated))
            self.assertIn('saved and independently checked',reply)
            self.assertEqual(r.budget.used,1)
            self.assertIn('no pending',await router.answer(fixtures.history('Confirm.',reply)))
    async def test_uncertain_confirmation_cannot_restore_expired_proposal(self):
        async with self.action_router() as (router,r):
            prompt=await router.answer(fixtures.history('Please save three notebooks.'))
            router.pending['expires']=0
            self.assertIn('expired',await router.answer(fixtures.history('Confirm?',prompt)))
            self.assertIsNone(router.pending);self.assertEqual(r.budget.used,0)

    async def test_inverted_only_question_preserves_proposal_without_a_write(self):
        async with self.action_router() as (router,r):
            prompt=await router.answer(fixtures.history('Please save three notebooks.'))
            reply=await router.answer(fixtures.history('\u00bfConfirm',prompt))
            self.assertEqual(reply,prompt);self.assertIsNotNone(router.pending)
            self.assertEqual(r.budget.used,0)
            with contextlib.closing(sqlite3.connect(self.folder/'test.sqlite3')) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM items').fetchone()[0],0)

    async def test_confirmed_statement_uses_existing_pending_action_without_model_or_duplicate(self):
        async with self.action_router() as (router,r):
            prompt=await router.answer(fixtures.history('Please save three notebooks.'))
            original=router.pending['operation_id'];router.ordinary=fixtures.Model()
            reply=await router.answer(fixtures.history('Confirmed.',prompt))
            self.assertIn('saved and independently checked',reply);self.assertEqual(r.budget.used,1)
            self.assertEqual(r.journal.records[original]['state'],'VERIFIED')
            self.assertEqual(router.ordinary.seen,[])
            self.assertIn('no pending',await router.answer(fixtures.history('Confirmed.',reply)))
            self.assertEqual(r.budget.used,1)
            with contextlib.closing(sqlite3.connect(self.folder/'test.sqlite3')) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM items').fetchone()[0],1)

    async def test_confirmed_questions_preserve_same_proposal_without_model_or_effect(self):
        async with self.action_router() as (router,r):
            prompt=await router.answer(fixtures.history('Please save three notebooks.'))
            original=router.pending['operation_id'];router.ordinary=fixtures.Model()
            for text in ('Confirmed?','¿Confirmed?','¿Confirmed'):
                self.assertEqual(await router.answer(fixtures.history(text,prompt)),prompt)
                self.assertEqual(router.pending['operation_id'],original)
            self.assertEqual(r.budget.used,0);self.assertEqual(router.ordinary.seen,[])

    async def test_confirmed_cannot_authorize_unheard_prompt(self):
        async with self.action_router() as (router,r):
            prompt=await router.answer(fixtures.history('Please save three notebooks.'));router.ordinary=fixtures.Model()
            self.assertEqual(await router.answer(fixtures.history('Confirmed.',prompt,'cancelled')),prompt)
            self.assertEqual(r.budget.used,0);self.assertIsNotNone(router.pending)

    async def test_confirmed_cannot_restore_expired_proposal(self):
        async with self.action_router() as (router,r):
            prompt=await router.answer(fixtures.history('Please save three notebooks.'));router.pending['expires']=0;router.ordinary=fixtures.Model()
            self.assertIn('expired',await router.answer(fixtures.history('Confirmed.',prompt)))
            self.assertIsNone(router.pending);self.assertEqual(r.budget.used,0)

    async def test_nonaffirmative_confirmed_phrases_never_commit(self):
        async with self.action_router() as (router,r):
            for text in ('Not confirmed.','Confirmed yesterday.','I have not confirmed.'):
                router.ordinary=fixtures.Model(fixtures.decision('propose_shopping_item',{'item':'notebooks','quantity':3}))
                prompt=await router.answer(fixtures.history('Please save three notebooks.'))
                router.ordinary=fixtures.Model(fixtures.decision(text='Nothing was executed.'))
                self.assertEqual(await router.answer(fixtures.history(text,prompt)),'Nothing was executed.')
                self.assertIsNone(router.pending);self.assertEqual(r.budget.used,0)
            with contextlib.closing(sqlite3.connect(self.folder/'test.sqlite3')) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM items').fetchone()[0],0)
