import asyncio,json,unittest
from types import SimpleNamespace
from speech_mcp.runtime import supervise
from speech_mcp.diagnostics import make_fault_observer,status_message
from speech_mcp.controller import TurnController

class Events:
    def __init__(self):self.rows=[]
    def emit(self,event,**fields):self.rows.append({'event':event,**fields})
class Runner:
    def __init__(self,order,fail=False):self.order=order;self.fail=fail;self.started=asyncio.Event();self.stop=asyncio.Event();self.finished=False
    async def run(self):
        self.started.set()
        try:
            if self.fail:raise RuntimeError('synthetic runner error')
            await self.stop.wait()
        finally:self.finished=True;self.order.append('worker_stopped')
    async def cancel(self):self.order.append('cancel_requested');self.stop.set()
class Controller:
    def __init__(self,order):self.order=order
    async def close(self):self.order.append('controller_closed')
class Transport:
    def __init__(self,order,runner):self.order=order;self.runner=runner
    async def close(self):
        if not self.runner.finished:raise AssertionError('Premature device closure')
        self.order.append('audio_closed')

class SupervisionTests(unittest.IsolatedAsyncioTestCase):
    def objects(self,fail=False):
        order=[];runner=Runner(order,fail);return order,runner,Controller(order),Transport(order,runner),Events()
    async def assert_stop(self,kind):
        order,runner,controller,transport,events=self.objects();quota=asyncio.Event();fault=asyncio.Event()
        if kind=='provider_quota':quota.set()
        if kind=='pipeline_fault':fault.set()
        reason=await supervise(runner,controller,transport,events,quota,fault,.02)
        self.assertEqual(reason,kind);self.assertTrue(runner.finished)
        self.assertLess(order.index('worker_stopped'),order.index('audio_closed'))
        self.assertEqual(events.rows[-1]['event'],'session_stop')
    async def test_service_error_stops_before_audio_teardown(self):
        order,runner,controller,transport,events=self.objects();failure=asyncio.Event();failure.set()
        reason=await supervise(runner,controller,transport,events,asyncio.Event(),asyncio.Event(),1,service_failed=failure)
        self.assertEqual(reason,'speech_service_error');self.assertEqual(order[-1],'audio_closed')
        self.assertTrue(events.rows[-1]['cleanup_ok'])
    async def test_quota_waits_for_worker_before_closing_audio(self):await self.assert_stop('provider_quota')
    async def test_fault_waits_for_worker_before_closing_audio(self):await self.assert_stop('pipeline_fault')
    async def test_time_limit_waits_for_worker_before_closing_audio(self):await self.assert_stop('time_limit')
    async def test_caller_cancel_still_cleans_up_and_preserves_cancellation(self):
        order,runner,controller,transport,events=self.objects()
        task=asyncio.create_task(supervise(runner,controller,transport,events,asyncio.Event(),asyncio.Event(),10))
        await runner.started.wait();task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertTrue(runner.finished);self.assertEqual(order[-1],'audio_closed')
        self.assertEqual(events.rows[-1]['reason'],'caller_cancelled')
    async def test_runner_error_is_preserved_and_audio_closes_after_worker(self):
        order,runner,controller,transport,events=self.objects(True)
        with self.assertRaisesRegex(RuntimeError,'synthetic runner'):
            await supervise(runner,controller,transport,events,asyncio.Event(),asyncio.Event(),1)
        self.assertEqual(order[-1],'audio_closed');self.assertEqual(events.rows[-1]['reason'],'runner_error')
    async def test_normal_worker_completion_also_closes_audio(self):
        order,runner,controller,transport,events=self.objects();runner.stop.set()
        self.assertEqual(await supervise(runner,controller,transport,events,asyncio.Event(),asyncio.Event(),1),'completed')
        self.assertEqual(order[-1],'audio_closed')

class FaultAndStatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_error_observer_records_type_once_without_error_payload(self):
        from pipecat.frames.frames import ErrorFrame
        events=Events();failed=asyncio.Event();observer=make_fault_observer(events,failed)
        frame=ErrorFrame(error='synthetic-private-canary',exception=RuntimeError('synthetic-private-canary'))
        await observer.on_push_frame(SimpleNamespace(frame=frame));await observer.on_push_frame(SimpleNamespace(frame=frame))
        self.assertTrue(failed.is_set());self.assertEqual(len(events.rows),1)
        self.assertNotIn('synthetic-private-canary',json.dumps(events.rows))
    async def test_terminal_status_does_not_render_payload_fields(self):
        secret='synthetic-private-canary';fields={'error':secret,'api_key':secret,'text':secret,'transcript':secret,'reason':secret,'wait_seconds':secret,'role':'tts'}
        for event in ['turn_error','mcp_request_start','tts_rate_wait','pipeline_fault','session_stop']:
            with self.subTest(event=event):self.assertNotIn(secret,status_message(event,fields))
    async def test_closed_controller_cannot_spawn_new_provider_work(self):
        class Never:
            async def call(self,*args,**kwargs):raise AssertionError('Provider called after shutdown')
        async def no_play(*args):raise AssertionError('Playback after shutdown')
        controller=TurnController(Never(),Never(),Events(),no_play)
        await controller.close();generation=controller.clock.current
        controller.submit(bytes(6400),generation);controller.greet();await asyncio.sleep(0)
        await controller.close()
        self.assertFalse(controller.tasks);self.assertEqual(controller.clock.current,generation)

if __name__=='__main__':unittest.main()
