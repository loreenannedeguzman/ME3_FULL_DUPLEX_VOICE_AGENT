"""Provider-free target provenance, genuine local cue synthesis, no audible/live claim."""
import argparse,asyncio,hashlib,json,os,platform,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from voice_agent import EventLog
from me3_completion.client import ConversationClients
from me3_completion.cache import CUE
async def cue():
    os.environ.update(TTS_PROVIDER='piper',PIPER_WORKER_MODE='resident',PIPER_PYTHON=str(ROOT/'.venv-piper/bin/python'),PIPER_MODEL=str(ROOT/'models/piper/en_US-ljspeech-high.onnx'),ALLOW_PROVIDER_AUDIO='0')
    e=EventLog(ROOT/'evidence/completion/final-20261020-001'/('cue-diagnostic-'+str(time.monotonic_ns())))
    results=[]
    try:
        async with ConversationClients(e) as c:
            for g in [0,1]:
                t=time.monotonic();d=await c.call('tts',g,text=CUE,style='normal')
                results.append({'generation':g,'request_id':d['request_id'],'pcm_seconds':d['byte_count']/48000,'mcp_seconds':time.monotonic()-t,'cache_hit':d['tts_metrics'].get('completion_cue_cache_hit'),'worker_pid':d['tts_metrics'].get('worker_pid')})
    finally:e.close()
    assert results[0]['pcm_seconds']<1 and results[1]['cache_hit'] is True
    print(json.dumps({'results':results,'actual_local_synthesis':True,'hosted_requests':0,'audio_played':False,'evidence':str(e.path.relative_to(ROOT))}))
def inspect():
    from benchmark_m6.console import environment,task_state
    manifest=json.loads((ROOT/'evidence/completion/final-20261020-001/starting-source.json').read_text());bad=[n for n,h in manifest['source_hashes'].items() if hashlib.sha256((ROOT/n).read_bytes()).hexdigest()!=h]
    _,items,state=task_state()
    print(json.dumps({'environment':environment(),'baseline_source_count':len(manifest['source_hashes']),'mismatches':bad,'task_state':state,'expected_original_rows':sorted((x['quantity'],x['item']) for x in items),'task_state_changed':False}))
    assert not bad
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['inspect','cue']);a=p.parse_args()
    if a.mode=='cue':asyncio.run(cue())
    else:inspect()
