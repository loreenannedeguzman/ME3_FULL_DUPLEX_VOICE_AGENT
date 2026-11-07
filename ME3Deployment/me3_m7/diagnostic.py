"""Pi-only bounded actual-provider diagnostics on public generated clips; never live acceptance."""
import asyncio,base64,json,os,platform,re,time
from pathlib import Path
from dotenv import load_dotenv
from voice_agent import EventLog
from me3_m7.client import M7SpeechClients,RecoverableASRTimeout
from benchmark_m6.storage import atomic
ROOT=Path(__file__).resolve().parents[1]
CORPUS={'short':'What is two plus two?','long':'I have an early exam tomorrow and several chapters left to study. Give me one practical study tip in a single short sentence.'}
def words(text):return re.findall(r"[a-z0-9]+",text.lower())
def wer(reference,hypothesis):
    a,b=words(reference),words(hypothesis);row=list(range(len(b)+1))
    for i,x in enumerate(a,1):
        nxt=[i]
        for j,y in enumerate(b,1):nxt.append(min(nxt[-1]+1,row[j]+1,row[j-1]+(x!=y)))
        row=nxt
    return {'reference_words':len(a),'recognized_words':len(b),'edit_distance':row[-1],'wer':row[-1]/len(a)}
class Events(EventLog):
    def __init__(self,path):super().__init__(path);self.rows=[]
    def emit(self,event,**kw):self.rows.append({'event':event,**kw});super().emit(event,**kw)
async def run(folder):
    if platform.system()!='Linux' or 'Raspberry Pi' not in Path('/proc/device-tree/model').read_text():raise ValueError('target_Pi_required')
    load_dotenv(ROOT/'.env',encoding='utf-8-sig');os.environ.update(TTS_PROVIDER='piper',PIPER_WORKER_MODE='resident',PIPER_PYTHON=str(ROOT/'.venv-piper/bin/python'),PIPER_MODEL=str(ROOT/'models/piper/en_US-ljspeech-high.onnx'))
    events=Events(folder/'events');records=[];clips={};calls=0
    def save():atomic(folder/'measurements.json',{'scope':'generated diagnostic clips, not live microphone evidence','corpus':CORPUS,'asr_calls':calls,'max_asr_calls':12,'records':records,'raw_audio_or_transcripts_retained':False})
    async def attempt(client,g,case,profile,**kw):
        nonlocal calls
        if calls>=12:raise ValueError('diagnostic_budget_exhausted')
        calls+=1;client.profile=profile;start=time.monotonic_ns()
        try:
            result=await client.call('asr',g,pcm_b64=base64.b64encode(clips[case]).decode(),sample_rate=16000,**kw)
            row={'ordinal':calls,'generation':g,'case':case,'profile':profile,'status':'complete','client_ms':(time.monotonic_ns()-start)/1e6,**wer(CORPUS[case],result['text'])}
        except RecoverableASRTimeout:
            row={'ordinal':calls,'generation':g,'case':case,'profile':profile,'status':'controlled_timeout' if kw else 'timeout','client_ms':(time.monotonic_ns()-start)/1e6}
        except asyncio.CancelledError:
            row={'ordinal':calls,'generation':g,'case':case,'profile':profile,'status':'canceled','client_ms':(time.monotonic_ns()-start)/1e6};records.append(row);save();raise
        except Exception as exc:
            row={'ordinal':calls,'generation':g,'case':case,'profile':profile,'status':'error','error_type':type(exc).__name__,'client_ms':(time.monotonic_ns()-start)/1e6};records.append(row);save();raise
        records.append(row);save();print(json.dumps(row),flush=True);return row
    try:
        async with M7SpeechClients(events,profile='low') as client:
            import audioop
            for gen,(case,text) in enumerate(CORPUS.items(),1):
                data=await client.call('tts',gen,text=text,style='normal');pcm=base64.b64decode(data['pcm_b64']);clips[case]=audioop.ratecv(pcm,2,1,24000,16000,None)[0]
                if len(clips[case])>480000:raise ValueError('diagnostic_clip_over15s')
            gen=0
            for rep in (0,1):
                for case in ('short','long'):
                    for profile in (('low','minimal') if rep==0 else ('minimal','low')):
                        gen+=1;row=await attempt(client,gen,case,profile);row.update(rep=rep);save()
            await attempt(client,9,'short','low',deadline_ms=100)
            await attempt(client,10,'short','low')
            task=asyncio.create_task(attempt(client,11,'short','low'));observed=False;until=time.monotonic()+5
            while time.monotonic()<until and not task.done():
                starts=[x for x in events.rows if x['event']=='mcp_request_start' and x.get('role')=='asr' and x.get('generation')==11]
                if starts:
                    rid=starts[-1]['request_id'];lines=(events.path.parent/'asr-server.stderr.log').read_text(encoding='utf-8').splitlines()
                    for line in lines:
                        try:row=json.loads(line)
                        except ValueError:continue
                        if row.get('event')=='m7_http_request_start' and row.get('request_id')==rid:observed=True;break
                if observed:break
                await asyncio.sleep(.05)
            if task.done():raise ValueError('cancellation_probe_completed_before_cancel')
            events.emit('m7_diagnostic_cancel_requested',generation=11,http_start_observed=observed);task.cancel()
            try:await task
            except asyncio.CancelledError:pass
            await attempt(client,12,'short','low')
    finally:clips.clear();events.close();save()
    pairs=[]
    for rep in (0,1):
        for case in ('short','long'):
            low=next(x for x in records if x.get('rep')==rep and x['case']==case and x['profile']=='low');candidate=next(x for x in records if x.get('rep')==rep and x['case']==case and x['profile']=='minimal')
            pairs.append({'rep':rep,'case':case,'low':low,'minimal':candidate})
    import statistics
    complete=all(p['low']['status']==p['minimal']['status']=='complete' for p in pairs)
    faster=sum(p['minimal'].get('client_ms',float('inf'))<p['low'].get('client_ms',0) for p in pairs)
    accuracy=all(p['minimal'].get('wer',float('inf'))<=p['low'].get('wer',-1) for p in pairs)
    selected='minimal' if complete and faster>=3 and accuracy and statistics.median(p['minimal']['client_ms'] for p in pairs)<statistics.median(p['low']['client_ms'] for p in pairs) else 'low'
    summary={'scope':'small matched same generated PCM diagnostic; not live acoustic or population evidence','pairs':pairs,'selected_profile':selected,'selection_rule':'all4pairs complete; candidate faster in>=3/4 and lower median; no pair WER regression','complete_pairs':complete,'faster_pairs':faster,'no_observed_wer_regression':accuracy,'calls':calls,'controlled_timeout_recovery':next(x for x in records if x['generation']==10)['status']=='complete','cancel_then_replacement':next(x for x in records if x['generation']==12)['status']=='complete','cancel_http_start_observed':observed}
    atomic(folder/'summary.json',summary);print(json.dumps(summary),flush=True)
if __name__=='__main__':
    import sys
    folder=ROOT/'evidence/m7'/sys.argv[1];folder.mkdir(exist_ok=False,parents=True);asyncio.run(run(folder))
