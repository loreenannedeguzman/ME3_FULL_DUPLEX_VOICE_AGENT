import asyncio,contextlib,io,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from me3_agent.runner import read_controls


class TimeoutControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_session_can_arm_once_without_erasing_historical_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);old=root/'controlled-asr-timeout-armed.json';old.write_text('historical immutable marker')
            session=root/'sessions/live015';session.mkdir(parents=True)
            armed=[];emitted=[]
            events=SimpleNamespace(path=session/'events.jsonl',active='E10_timeout',processor=SimpleNamespace(controller=SimpleNamespace(task=None)),speech=SimpleNamespace(arm_controlled_timeout=lambda:armed.append(True)),emit=lambda *a,**k:emitted.append(a[0]))
            reader=asyncio.StreamReader()
            reader.feed_data(b'{"action":"arm_asr_timeout"}\n{"action":"arm_asr_timeout"}\n{"action":"stop"}\n');reader.feed_eof()
            out=io.StringIO()
            with contextlib.redirect_stdout(out):await read_controls(events,reader)
            self.assertEqual(armed,[True])
            self.assertIn('controlled_timeout_already_armed_this_session',out.getvalue())
            self.assertEqual(old.read_text(),'historical immutable marker')
            self.assertTrue(json.loads((session/'controlled-asr-timeout-armed.json').read_text())['one_shot'])

    async def test_active_work_cannot_be_armed(self):
        with tempfile.TemporaryDirectory() as tmp:
            armed=[]
            events=SimpleNamespace(path=Path(tmp)/'events.jsonl',processor=SimpleNamespace(controller=SimpleNamespace(task=SimpleNamespace(done=lambda:False))),speech=SimpleNamespace(arm_controlled_timeout=lambda:armed.append(True)),emit=lambda *a,**k:None)
            reader=asyncio.StreamReader();reader.feed_data(b'{"action":"arm_asr_timeout"}\n{"action":"stop"}\n');reader.feed_eof()
            out=io.StringIO()
            with contextlib.redirect_stdout(out):await read_controls(events,reader)
            self.assertIn('work_not_settled',out.getvalue());self.assertEqual(armed,[])
