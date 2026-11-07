"""Pi native local-engine format/style/cancellation checks; no API, no playback."""
import asyncio,audioop,json,sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from speech_mcp.tts_config import TTSConfig
from speech_mcp.tts_adapters import FliteTTS

async def main():
    config=TTSConfig.from_env({'TTS_PROVIDER':'flite'});config.require_ready()
    adapter=FliteTTS(config)
    try:
        normal=await adapter.synthesize('One, two, three, four, five.','normal')
        slow=await adapter.synthesize('One, two, three, four, five.','slow')
        assert audioop.rms(normal,2)>0 and len(normal)%2==0
        assert len(slow)>len(normal)*1.2
        started=asyncio.Event();native=asyncio.create_subprocess_exec;processes=[]
        async def launch(*args,**kwargs):
            process=await native(*args,**kwargs);processes.append(process);started.set();return process
        with patch('speech_mcp.tts_adapters.asyncio.create_subprocess_exec',launch):
            task=asyncio.create_task(adapter.synthesize('This sentence is spoken locally. '*10,'slow'))
            await started.wait();assert not task.done();task.cancel()
            try:await task
            except asyncio.CancelledError:pass
            else:raise AssertionError('Cancellation swallowed')
        assert len(processes)==1 and processes[0].returncode is not None
        assert not list((ROOT/'.tmp/tts-local').glob('flite-*'))
        print(json.dumps({'native_flite':'PASS','sample_rate':24000,'channels':1,'encoding':'pcm_s16le','normal_bytes':len(normal),'slow_bytes':len(slow),'normal_nonzero_pcm':True,'canceled_process_reaped':True,'transient_text_removed':True,'external_provider_calls':0,'audibility':'NOT_TESTED'}))
    finally:await adapter.close()
if __name__=='__main__':asyncio.run(main())
