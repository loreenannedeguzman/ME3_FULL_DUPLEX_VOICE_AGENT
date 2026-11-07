"""Actual capture/MCP boundary with synthetic PCM and fake recognition only."""
import asyncio,base64,unittest
from unittest.mock import AsyncMock,patch
from pipecat.audio.vad.vad_analyzer import VADState
from tests import test_completion_pipeline as fixture
from me3_assistant.groq_server import Service
from me3_assistant.groq_backend import GroqASR

class LongInputTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixture.PipelineTests.asyncSetUp
    asyncTearDown=fixture.PipelineTests.asyncTearDown
    feed=fixture.PipelineTests.feed
    settle=fixture.PipelineTests.settle
    async def test_long_transcript_reaches_reasoning_without_silent_cutoff(self):
        self.p.max_pcm_bytes=3840000;self.p.controller.max_transcript_chars=4000
        self.p.endpoint.complete=True;self.clients.answer='A natural detailed request. '*60
        await self.feed(VADState.SPEAKING,1000);await self.feed(VADState.QUIET)
        await self.settle(lambda:self.p.controller.task is not None and self.p.controller.task.done())
        users=[r for r in self.p.controller.history if r['type']=='user_input']
        self.assertEqual(users[0]['content'][0]['text'],self.clients.answer.strip())
        self.assertFalse(any(e['event']=='turn_error' for e in self.events.rows))
    async def test_twenty_second_capture_is_complete_and_endpoint_uses_tail_only(self):
        self.p.max_pcm_bytes=3840000;self.p.endpoint.complete=True
        await self.feed(VADState.SPEAKING,1000);await self.feed(VADState.QUIET)
        await self.settle(lambda:self.p.controller.task is not None and self.p.controller.task.done())
        calls=[c for c in self.clients.calls if c[0]=='asr']
        self.assertEqual(len(calls),1);self.assertGreater(len(base64.b64decode(calls[0][2]['pcm_b64'])),480000)
        self.assertEqual(len(self.p.endpoint.calls[0][0]),480000)
        self.assertFalse(any(e['event']=='utterance_limit_reached' for e in self.events.rows))
    async def test_continuation_after_long_capture_keeps_prefix_and_only_one_asr(self):
        self.p.max_pcm_bytes=3840000;self.p.endpoint.complete=False
        await self.feed(VADState.SPEAKING,900);await self.feed(VADState.QUIET)
        await self.settle(lambda:any(e['event']=='completion_turn_held' for e in self.events.rows));before=bytes(self.p.pcm)
        self.p.endpoint.complete=True;await self.feed(VADState.SPEAKING,50);await self.feed(VADState.QUIET)
        await self.settle(lambda:self.p.controller.task is not None and self.p.controller.task.done())
        calls=[c for c in self.clients.calls if c[0]=='asr'];self.assertEqual(len(calls),1)
        self.assertTrue(base64.b64decode(calls[0][2]['pcm_b64']).startswith(before))
    async def test_overflow_rejects_whole_turn_without_capturing_tail_as_new_request(self):
        self.p.max_pcm_bytes=64000
        await self.feed(VADState.SPEAKING,120);await self.feed(VADState.QUIET)
        await self.settle(lambda:self.p.controller.task is not None and self.p.controller.task.done())
        self.assertFalse(any(c[0]=='asr' for c in self.clients.calls));self.assertFalse(self.p.pcm);self.assertFalse(self.p.preroll)

class ASRBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_stdio_mcp_carries120_second_payload_without_truncation(self):
        import os,sys
        from pathlib import Path
        from contextlib import AsyncExitStack
        from datetime import timedelta
        from mcp import ClientSession,StdioServerParameters
        from mcp.client.stdio import stdio_client
        from speech_mcp.client import SpeechClients
        async with AsyncExitStack() as stack:
            stderr=stack.enter_context(open(os.devnull,'w'))
            streams=await stack.enter_async_context(stdio_client(StdioServerParameters(command=sys.executable,args=['-B','-m','tests.long_input_mcp_fixture'],cwd=str(Path(__file__).resolve().parents[1])),errlog=stderr))
            session=await stack.enter_async_context(ClientSession(*streams,read_timeout_seconds=timedelta(seconds=15)))
            await session.initialize()
            result=SpeechClients.unpack(await session.call_tool('transcribe',{'generation':1,'request_id':'asr-120-seconds','pcm_b64':base64.b64encode(bytes(3840000)).decode()}))
            self.assertTrue(result['ok']);self.assertTrue(result['cleanup_ok']);self.assertEqual(result['text'],'Captured 3840000 bytes.')
    async def test_normal_groq_service_accepts_long_pcm_and_returns_without_truncating(self):
        received=[]
        class Provider:
            async def transcribe(self,pcm,*args):received.append(pcm);return {'text':'x'*1500}
            async def close(self):pass
        service=Service(lambda emit:Provider(),writer=lambda *a,**k:None)
        pcm=b'\1\0'*320000
        result=await service.call(base64.b64encode(pcm).decode(),1,'asr-long')
        self.assertEqual(received,[pcm]);self.assertTrue(result['cleanup_ok']);self.assertEqual(len(result['text']),1500)
    async def test_service_rejects_over120_before_provider_creation(self):
        factory=AsyncMock();service=Service(factory,writer=lambda *a,**k:None)
        with self.assertRaises(ValueError):await service.call(base64.b64encode(bytes(3840002)).decode(),1,'asr-too-long')
        factory.assert_not_called()
    async def test_adapter_posts_full_long_audio_once(self):
        transport=AsyncMock();transport.request.return_value={'text':'A complete long request.'}
        client=GroqASR(lambda *a,**kw:None,transport=transport)
        result=await client.transcribe(bytes(640000),'low')
        self.assertEqual(result['text'],'A complete long request.');transport.request.assert_awaited_once()

if __name__=='__main__':unittest.main()
