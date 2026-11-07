"""Actual Pi MCP cancellation of a local neural worker; no audio playback/cloud call."""
import asyncio,json,os,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from speech_mcp.client import SpeechClients
from voice_agent import EventLog

def workers():
    found=[]
    for p in Path('/proc').iterdir():
        if not p.name.isdigit():continue
        try:args=(p/'cmdline').read_bytes().split(b'\0')
        except (FileNotFoundError,PermissionError,ProcessLookupError):continue
        if any(a.endswith(b'/speech_mcp/piper_worker.py') for a in args):found.append(int(p.name))
    return found
async def run(events):
    if workers():raise RuntimeError('preexisting_worker')
    async with SpeechClients(events) as clients:
        task=asyncio.create_task(clients.call('tts',7,text='This fixed diagnostic sentence must be cancelled before playback.',style='normal'))
        started=time.perf_counter();seen=[]
        while not seen and not task.done() and time.perf_counter()-started<6:
            await asyncio.sleep(.01);seen=workers()
        if not seen or task.done():raise RuntimeError('worker_not_observed_before_cancellation')
        task.cancel()
        try:await task
        except asyncio.CancelledError:pass
        else:raise RuntimeError('cancellation_not_propagated')
        deadline=time.perf_counter()+4
        while workers() and time.perf_counter()<deadline:await asyncio.sleep(.02)
        if workers():raise RuntimeError('worker_not_reaped')
        events.emit('piper_real_mcp_cancel_pass',generation=7,observed_worker_pids=seen,remaining_worker_pids=[],generated_playback=False)
    print(json.dumps({'actual_mcp_cancel':'PASS','worker_seen':seen,'worker_reaped':True,'asr_calls':0,'cloud_tts_calls':0,'audibility':'NOT_TESTED'}))
if __name__=='__main__':
    os.environ['TTS_PROVIDER']='piper';os.environ['PIPER_PYTHON']=str(ROOT/'.venv-piper/bin/python');os.environ['PIPER_MODEL']=str(ROOT/'models/piper/en_US-ljspeech-high.onnx')
    events=EventLog(ROOT/'evidence/m5'/('cancel-'+str(time.monotonic_ns())))
    try:asyncio.run(run(events))
    finally:events.close()
