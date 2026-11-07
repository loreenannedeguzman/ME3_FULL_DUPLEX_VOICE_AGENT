"""Only the remaining four approved ASR calls; no failed pair is relaunched."""
import asyncio,base64,json,os,platform,time
from pathlib import Path
from dotenv import load_dotenv
from voice_agent import EventLog
from benchmark_m6.storage import atomic,read
from me3_m7.client import M7SpeechClients,RecoverableASRTimeout
from me3_m7.codec import encode,decode
from me3_m7.diagnostic import Events,CORPUS,wer
ROOT=Path(__file__).resolve().parents[1]
async def run(folder):
    if platform.system()!='Linux':raise ValueError('target_Pi_required')
    old=read(ROOT/'evidence/m7/diagnostic-001/measurements.json');assert old['asr_calls']==8
    load_dotenv(ROOT/'.env',encoding='utf-8-sig');os.environ.update(TTS_PROVIDER='piper',PIPER_WORKER_MODE='resident',PIPER_PYTHON=str(ROOT/'.venv-piper/bin/python'),PIPER_MODEL=str(ROOT/'models/piper/en_US-ljspeech-high.onnx'))
    budget=ROOT/'evidence/m7/diagnostic-budget.json'
    if budget.exists():raise ValueError('diagnostic_remaining_budget_already_reserved')
    atomic(budget,{'prior_calls':8,'reserved_total':8,'cap':12});events=Events(folder/'events');records=[];pcm=None
    async def attempt(client,g,format,deadline=30000):
        state=read(budget)
        if state['reserved_total']>=12:raise ValueError('diagnostic_budget_exhausted')
        state['reserved_total']+=1;atomic(budget,state);start=time.monotonic_ns()
        try:
            data=await client.call('asr',g,pcm_b64=base64.b64encode(pcm).decode(),sample_rate=16000,audio_format=format,deadline_ms=deadline)
            row={'generation':g,'format':format,'status':'complete','elapsed_ms':(time.monotonic_ns()-start)/1e6,**wer(CORPUS['long'],data['text'])}
        except RecoverableASRTimeout:row={'generation':g,'format':format,'status':'controlled_timeout' if deadline!=30000 else 'timeout','elapsed_ms':(time.monotonic_ns()-start)/1e6}
        except asyncio.CancelledError:
            row={'generation':g,'format':format,'status':'canceled','elapsed_ms':(time.monotonic_ns()-start)/1e6};records.append(row);save();raise
        except Exception as exc:
            row={'generation':g,'format':format,'status':'error','error_type':type(exc).__name__,'elapsed_ms':(time.monotonic_ns()-start)/1e6};records.append(row);save();raise
        records.append(row);save();print(json.dumps(row),flush=True);return row
    def save():atomic(folder/'measurements.json',{'scope':'generated same-PCM lifecycle/format diagnostic, not live acceptance','records':records,'budget':read(budget),'no_audio_or_transcripts_retained':True})
    try:
        async with M7SpeechClients(events,profile='low') as client:
            import audioop
            d=await client.call('tts',0,text=CORPUS['long'],style='normal');pcm=audioop.ratecv(base64.b64decode(d['pcm_b64']),2,1,24000,16000,None)[0]
            if len(pcm)>480000:raise ValueError('diagnostic_clip_size')
            flac,_=await encode(pcm,'flac');roundtrip=await decode(flac);assert roundtrip==pcm
            atomic(folder/'codec.json',{'pcm_bytes':len(pcm),'flac_bytes':len(flac),'wav_bytes':len(pcm)+44,'roundtrip_byte_identical':True,'sample_rate':16000,'channels':1})
            await attempt(client,9,'wav',100);flac_result=await attempt(client,10,'flac')
            task=asyncio.create_task(attempt(client,11,'wav'));observed=False;until=time.monotonic()+8
            while time.monotonic()<until and not task.done():
                starts=[x for x in events.rows if x['event']=='mcp_request_start' and x.get('generation')==11 and x.get('role')=='asr']
                if starts:
                    rid=starts[-1]['request_id']
                    for line in (events.path.parent/'asr-server.stderr.log').read_text().splitlines():
                        try:x=json.loads(line)
                        except ValueError:continue
                        if x.get('request_id')==rid and x.get('event')=='m7_transport_request_chunk_sent':observed=True;break
                if observed:break
                await asyncio.sleep(.05)
            if task.done():raise ValueError('cancel_probe_completed_before_cancellation')
            await asyncio.sleep(.05);events.emit('m7_diagnostic_cancel_requested',generation=11,body_submission_observed=observed);task.cancel()
            try:await task
            except asyncio.CancelledError:pass
            wav_result=await attempt(client,12,'wav')
            selected='flac' if flac_result['status']=='complete' and flac_result['edit_distance']<=1 and (wav_result['status']!='complete' or flac_result['wer']<=wav_result['wer']) else 'wav'
            atomic(folder/'summary.json',{'selected_audio_format':selected,'profile':'low','scope':'one same-PCM long-clip format feasibility pair only; improvement requires comparable live benchmark','roundtrip_byte_identical':True,'flac':flac_result,'wav':wav_result,'body_submission_before_cancel_observed':observed,'cancel_release_barrier_completed':any(x['event']=='m7_release_barrier_complete' for x in events.rows),'total_reserved_asr_calls':read(budget)['reserved_total']})
    finally:pcm=None;events.close();save()
if __name__=='__main__':
    import sys
    folder=ROOT/'evidence/m7'/sys.argv[1];folder.mkdir(parents=True,exist_ok=False);asyncio.run(run(folder))
