"""Provider-free ordered voice shutdown; no device or provider initialization."""
import asyncio,unittest
from types import SimpleNamespace
from unittest.mock import patch
from me3_assistant.gui import Console,Server
from speech_mcp.runtime import supervise
from tests.test_assistant_mode import config

class StopTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_failure_requests_owner_shutdown_once_and_keeps_loop_responsive(self):
        c=Console(config('extension'),{'revision':'fixture'});c.mode='voice';c.stop_event=asyncio.Event();ready=asyncio.Event();cleaning=asyncio.Event();release=asyncio.Event();seen=[]
        async def run(*a,stop_requested,**kw):
            try:
                ready.set();await stop_requested.wait();cleaning.set();await release.wait();seen.append('closed')
            except asyncio.CancelledError:seen.append('unsafe_owner_cancel');raise
        with patch('me3_assistant.runner.run',run):
            c.task=asyncio.create_task(c.voice());await ready.wait()
            c.observe('turn_error',stage='reasoning');await cleaning.wait()
            duplicate=asyncio.create_task(c.stop());await asyncio.sleep(0)
            self.assertFalse(c.task.done());self.assertEqual(c.state,'stopping');self.assertIsNotNone(c.public_error)
            release.set();await asyncio.wait_for(duplicate,1)
            self.assertEqual(seen,['closed']);self.assertEqual(c.state,'stopped');self.assertTrue(c.task.done())
    async def test_disconnected_stop_caller_cannot_abort_owner_cleanup(self):
        c=Console(config(),{'revision':'fixture'});c.mode='voice';c.stop_event=asyncio.Event();closing=asyncio.Event();release=asyncio.Event()
        async def owner():await c.stop_event.wait();closing.set();await release.wait()
        c.task=asyncio.create_task(owner());caller=asyncio.create_task(c.stop());await closing.wait();caller.cancel()
        with self.assertRaises(asyncio.CancelledError):await caller
        self.assertFalse(c.task.cancelled());release.set();await c.task;await c.stop();self.assertEqual(c.state,'stopped')
    async def test_supervisor_stops_on_explicit_event_and_orders_worker_before_transport(self):
        end=asyncio.Event();stop=asyncio.Event();started=asyncio.Event();order=[]
        class Runner:
            async def run(self):started.set();await end.wait();order.append('worker_closed')
            async def cancel(self):order.append('worker_cancel');end.set()
        class Controller:
            async def close(self):order.append('controller_closed')
        class Transport:
            async def close(self):order.append('transport_closed')
        rows=[];events=SimpleNamespace(emit=lambda e,**kw:rows.append((e,kw)))
        task=asyncio.create_task(supervise(Runner(),Controller(),Transport(),events,asyncio.Event(),asyncio.Event(),30,stop_requested=stop))
        await started.wait();stop.set();self.assertEqual(await asyncio.wait_for(task,1),'caller_cancelled')
        self.assertEqual(order,['controller_closed','worker_cancel','worker_closed','transport_closed']);self.assertTrue(rows[-1][1]['cleanup_ok'])
    async def test_stop_already_set_before_supervision_does_not_run_until_session_timeout(self):
        stop=asyncio.Event();stop.set();end=asyncio.Event()
        async def worker():await end.wait()
        async def cancel():end.set()
        async def close():pass
        reason=await asyncio.wait_for(supervise(SimpleNamespace(run=worker,cancel=cancel),SimpleNamespace(close=close),SimpleNamespace(close=close),SimpleNamespace(emit=lambda *a,**k:None),asyncio.Event(),asyncio.Event(),30,stop_requested=stop),1)
        self.assertEqual(reason,'caller_cancelled')

class DispatchTests(unittest.TestCase):
    def test_timed_out_dispatch_cancels_queued_work_instead_of_running_it_later(self):
        class Future:
            canceled=False
            def result(self,timeout):raise TimeoutError()
            def cancel(self):self.canceled=True
        f=Future();coro=object()
        with patch('asyncio.run_coroutine_threadsafe',return_value=f),self.assertRaises(TimeoutError):Server.dispatch(SimpleNamespace(loop=object()),coro)
        self.assertTrue(f.canceled)
