"""Actual Pi model cancel/reap/restart/warm/shutdown. No acoustic claim."""
import asyncio,json,os,sys,time,argparse
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.compare_voice import settings,Log
from speech_mcp.client import SpeechClients

def ticks(pid):
    data=Path('/proc',str(pid),'stat').read_text().split(') ',1)[1].split();return int(data[11])+int(data[12])
async def run(name):
    settings('piper');os.environ['PIPER_WORKER_MODE']='resident';folder=ROOT/'evidence/m5/reuse'/name;folder.mkdir(parents=True,exist_ok=False);events=Log(folder/'mcp');result={'status':'FAIL','hosted_calls':0,'acoustic_test':False}
    try:
        async with SpeechClients(events) as clients:
            a=await clients.call('tts',1,text='The model is ready.',style='normal');pid=a['tts_metrics']['worker_pid'];baseline=ticks(pid)
            task=asyncio.create_task(clients.call('tts',2,text='Please carefully count each number and explain why it matters. '*10,style='normal'))
            async with asyncio.timeout(8):
                while ticks(pid)<=baseline and not task.done():await asyncio.sleep(.01)
            if task.done():raise RuntimeError('Request completed before cancel observation')
            result['worker_cpu_ticks_before']=baseline;result['worker_cpu_ticks_at_cancel']=ticks(pid);result['canceled_worker_pid']=pid
            began=time.perf_counter();task.cancel()
            try:await task
            except asyncio.CancelledError:pass
            async with asyncio.timeout(3):await clients.sessions['tts'].cancelled_settled.wait()
            result['cancel_to_settled_seconds']=time.perf_counter()-began;result['old_worker_reaped_at_cancel_reply']=not Path('/proc',str(pid)).exists()
            b=await clients.call('tts',3,text='The replacement answer is four.',style='normal');new=b['tts_metrics']['worker_pid'];assert new!=pid and b['tts_metrics']['cold_start'] and b['tts_metrics']['worker_epoch']==2
            result['old_worker_reaped_before_replacement_result']=not Path('/proc',str(pid)).exists();assert result['old_worker_reaped_before_replacement_result']
            c=await clients.call('tts',4,text='We can continue the conversation.',style='normal');assert c['tts_metrics']['worker_pid']==new and not c['tts_metrics']['cold_start']
            result['replacement_metrics']=b['tts_metrics'];result['followup_metrics']=c['tts_metrics']
        result['final_worker_reaped']=not Path('/proc',str(new)).exists();assert result['final_worker_reaped'];result['status']='PASS'
    finally:
        events.close();(folder/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result,indent=2))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--folder',required=True);args=p.parse_args()
    if args.folder not in {'cancel-002','cancel-003'}:raise SystemExit('Choose fresh bounded evidence name')
    asyncio.run(run(args.folder))
