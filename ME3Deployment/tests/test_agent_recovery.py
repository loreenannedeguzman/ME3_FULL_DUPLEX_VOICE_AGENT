import asyncio,base64,unittest
from types import SimpleNamespace
from me3_agent.controller import AgentController
from me3_completion.playback import PlaybackGate

class Events:
    def __init__(self):self.rows=[]
    def emit(self,event,**kw):self.rows.append({'event':event,**kw})

class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_deadline_clears_suspended_playback_before_notice_and_next_turn(self):
        gate=PlaybackGate(SimpleNamespace(suspend=lambda:None,resume=lambda:None))
        calls=[];events=Events()
        class Clients:
            async def call(self,role,g,**kw):
                if role=='asr':return {'text':'Hello.','generation':g,'request_id':'asr-test'}
                return {'pcm_b64':base64.b64encode(bytes(960)).decode()}
        async def answer(*args):return 'Hello.'
        async def play(pcm,g):
            calls.append(('play',g));await gate.wait();calls.append(('heard',g))
        c=AgentController(Clients(),SimpleNamespace(answer=answer,pending=None),events,play,turn_deadline_seconds=1)
        # Force the real timeout path without waiting through production credits.
        c.credit_synthesis=lambda g:None
        original=c.play_audio
        async def uncredited(*args):return await play(*args)
        c.play_audio=uncredited
        async def invalidate(g):calls.append(('clear',g));gate.resume();return True
        c.invalidate_timed_out_output=invalidate
        gate.suspend();g=c.interrupt();c.submit(bytes(6400),g)
        try:
            await asyncio.wait_for(asyncio.shield(c.task),2)
        except TimeoutError:self.fail('Timeout notice remained blocked behind suspended old output')
        self.assertEqual(calls,[('play',g),('clear',g),('play',g),('heard',g)])
        self.assertFalse(c.deadlines)
        g=c.interrupt();c.submit(bytes(6400),g);await c.task
        self.assertEqual(sum(r['event']=='core_turn_deadline' for r in events.rows),1)
        await c.close()

    async def test_cleanup_cancels_probe_and_invalidates_buffer(self):
        from me3_agent.recovery import attach
        calls=[];started=asyncio.Event()
        async def probe():
            started.set()
            try:await asyncio.Event().wait()
            finally:calls.append('probe_closed')
        p=SimpleNamespace(controller=SimpleNamespace(current=lambda g:g==3),probe=asyncio.create_task(probe()),probing=True)
        async def clear(reason):calls.append(reason)
        p.clear_output=clear;await started.wait();attach(p,Events())
        self.assertTrue(await p.controller.invalidate_timed_out_output(3))
        self.assertEqual(calls,['probe_closed','expired_response']);self.assertFalse(p.probing)
        self.assertFalse(await p.controller.invalidate_timed_out_output(2));self.assertEqual(len(calls),2)

    async def test_cleanup_cannot_clear_new_generation_during_probe_settlement(self):
        from me3_agent.recovery import attach
        current=[3];calls=[];started=asyncio.Event()
        async def probe():
            started.set()
            try:await asyncio.Event().wait()
            finally:current[0]=4
        p=SimpleNamespace(controller=SimpleNamespace(current=lambda g:g==current[0]),probe=asyncio.create_task(probe()),probing=True)
        async def clear(reason):calls.append(reason)
        p.clear_output=clear;await started.wait();attach(p,Events())
        self.assertFalse(await p.controller.invalidate_timed_out_output(3));self.assertFalse(calls)
