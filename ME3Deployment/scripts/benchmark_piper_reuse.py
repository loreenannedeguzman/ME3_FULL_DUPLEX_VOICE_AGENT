"""Local actual-MCP component comparison; no microphone/playback/hosted calls."""
import asyncio,hashlib,json,os,sys,time,statistics,argparse
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.compare_voice import CORPUS,Log,settings
from speech_mcp.client import SpeechClients

def save(folder,rows):
    (folder/'measurements.json').write_text(json.dumps({'utc':datetime.now(timezone.utc).isoformat(),'corpus':CORPUS,'records':rows,'scope':'actual TTS MCP complete PCM available; not acoustic or live speech; no hosted calls; model/process cold, OS page cache uncontrolled'},indent=2),encoding='utf-8')
async def run(folder):
    rows=[];settings('piper')
    for rep in range(2):
        for mode in (('per_request','resident') if rep==0 else ('resident','per_request')):
            os.environ['PIPER_WORKER_MODE']=mode;events=Log(folder/f'{rep}-{mode}')
            cases=list(CORPUS.items());cases=cases[rep:]+cases[:rep]
            try:
                async with SpeechClients(events) as clients:
                    for i,(case,text) in enumerate(cases):
                        called=time.perf_counter()
                        try:
                            d=await clients.call('tts',i,text=text,style='normal')
                            metrics=d.get('tts_metrics',{});pid=metrics.get('worker_pid');rss=None
                            if pid:
                                for line in Path('/proc',str(pid),'status').read_text().splitlines():
                                    if line.startswith('VmRSS:'):rss=int(line.split()[1])*1024
                            row={'rep':rep,'case':case,'mode':mode,'status':'PASS','mcp_full_pcm_seconds':time.perf_counter()-called,'backend_metrics':metrics,'audio_seconds':d['byte_count']/48000,'model':d['model'],'worker_rss_bytes':rss}
                        except Exception as e:
                            rows.append({'rep':rep,'case':case,'mode':mode,'status':'FAIL','error_type':type(e).__name__});save(folder,rows);raise
                        rows.append(row);save(folder,rows);print(json.dumps(row),flush=True)
            finally:events.close()
    pairs=[]
    for b in rows:
        if b['mode']!='resident' or b['backend_metrics']['cold_start']:continue
        a=next(a for a in rows if a['mode']=='per_request' and (a['rep'],a['case'])==(b['rep'],b['case']))
        pairs.append({'rep':b['rep'],'case':b['case'],'per_request_seconds':a['mcp_full_pcm_seconds'],'resident_seconds':b['mcp_full_pcm_seconds'],'saved_seconds':a['mcp_full_pcm_seconds']-b['mcp_full_pcm_seconds']})
    def stat(values):return {'n':len(values),'median_seconds':statistics.median(values),'range_seconds':[min(values),max(values)],'p95':'NOT_REPORTED_n_under20'}
    summary={'matched_warm_pairs':pairs,'matched_per_request':stat([x['per_request_seconds'] for x in pairs]),'matched_resident':stat([x['resident_seconds'] for x in pairs]),'cold_resident':stat([x['mcp_full_pcm_seconds'] for x in rows if x['mode']=='resident' and x['backend_metrics']['cold_start']]),'all_per_request':stat([x['mcp_full_pcm_seconds'] for x in rows if x['mode']=='per_request']),'no_hosted_calls':True,'all_pass':all(x['status']=='PASS' for x in rows),'voice_and_prosody_unchanged':True}
    assert summary['matched_resident']['median_seconds']<summary['matched_per_request']['median_seconds'],'Warm reduction criterion failed'
    (folder/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8');print(json.dumps(summary),flush=True)
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--folder',required=True);args=p.parse_args()
    folder=(ROOT/'evidence/m5/reuse'/args.folder).resolve();assert folder.is_relative_to((ROOT/'evidence/m5/reuse').resolve());folder.mkdir(parents=True,exist_ok=False)
    asyncio.run(run(folder))
