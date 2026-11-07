"""Fixed public-text actual TTS MCP comparison. Playback diagnostic is not live acceptance."""
import argparse,asyncio,base64,hashlib,json,logging,os,sys,time,wave,struct,statistics
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from voice_agent import EventLog,Settings,configure_pi_audio
from speech_mcp.client import SpeechClients
from speech_mcp.contracts import decode_pcm,MAX_TTS_SECONDS
CORPUS={
 'short':'Yes, I saved two notebooks to your shopping list.',
 'long':'You can review the shopping list whenever you like. Before I add anything else, I will tell you exactly what I intend to save and ask you to confirm it.',
 'question':'Would you like me to read the shopping list, or add another item?',
 'numbers':'There are 2 notebooks and 12 pens. The total is $12.50. Your appointment is on October 19 at 3:15 p.m.',
 'followup':'Yes, that is the city we were discussing. The Eiffel Tower is a famous landmark in Paris.'}
class Log(EventLog):
    def __init__(self,path):super().__init__(path);self.rows=[]
    def emit(self,event,**kw):super().emit(event,**kw);self.rows.append({'event':event,'monotonic_ns':time.monotonic_ns(),**kw})
def settings(provider):
    os.environ['TTS_PROVIDER']=provider
    if provider=='piper':
        os.environ['PIPER_PYTHON']=str(ROOT/'.venv-piper/bin/python')
        os.environ['PIPER_MODEL']=str(ROOT/'models/piper/en_US-ljspeech-high.onnx')
async def synthesize(folder):
    records=[]
    # Alternate provider order by repetition/case; no cloud model is invoked.
    for rep in range(2):
        for i,(case,text) in enumerate(CORPUS.items()):
            for provider in (['flite','piper'] if (rep+i)%2==0 else ['piper','flite']):
                settings(provider);events=Log(folder/(str(rep)+'-'+case+'-'+provider));started=time.perf_counter()
                try:
                    async with SpeechClients(events) as clients:
                        called=time.perf_counter();data=await clients.call('tts',0,text=text,style='normal');available=time.perf_counter()-called
                    pcm=decode_pcm(data['pcm_b64'],24000,MAX_TTS_SECONDS)
                    name=f'{rep}-{case}-{provider}.wav'
                    with wave.open(str(folder/name),'wb') as out:out.setnchannels(1);out.setsampwidth(2);out.setframerate(24000);out.writeframes(pcm)
                    samples=[x[0] for x in struct.iter_unpack('<h',pcm)]
                    records.append({'repetition':rep,'case':case,'provider':provider,'status':'PASS','tts_model':data['model'],'source_mime':data['source_mime'],'provider_api':data['provider_api'],'mcp_complete_pcm_seconds':available,'including_mcp_process_lifecycle_seconds':time.perf_counter()-started,'audio_seconds':len(pcm)/48000,'sample_rate':24000,'channels':1,'peak':max(abs(x) for x in samples),'full_scale_samples':sum(abs(x)>=32767 for x in samples),'backend_metrics':data.get('tts_metrics',{}),'file':name,'sha256':hashlib.sha256((folder/name).read_bytes()).hexdigest()})
                except Exception as exc:
                    records.append({'repetition':rep,'case':case,'provider':provider,'status':'FAIL','error_type':type(exc).__name__});raise
                finally:
                    events.close();(folder/'measurements.json').write_text(json.dumps({'corpus':CORPUS,'records':records,'scope':'fixed generated public text only; no microphone/ASR/text API request; software clocks, not acoustic latency'},indent=2))
                print(json.dumps(records[-1]),flush=True)
    summary={}
    for provider in ['flite','piper']:
        rows=[r for r in records if r['provider']==provider];values=[r['mcp_complete_pcm_seconds'] for r in rows]
        summary[provider]={'n':len(rows),'mcp_complete_pcm_median_seconds':statistics.median(values),'range_seconds':[min(values),max(values)],'failures':sum(r['status']!='PASS' for r in rows),'p95':'NOT_REPORTED_n_under20'}
    (folder/'summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary))
async def play_pair(folder,case,events):
    import mcp_voice_agent as app
    from pipecat.audio.vad.vad_analyzer import VADState
    ordered=['flite','piper'] if list(CORPUS).index(case)%2==0 else ['piper','flite']
    class NoCalls:
        async def call(self,*a,**k):raise RuntimeError('diagnostic_must_not_call_provider')
    config=Settings(api_key='',max_seconds=100);configure_pi_audio(config)
    runner,worker,transport,processor=app.build_pipeline(config,events,NoCalls(),None)
    async def quiet(_):return VADState.QUIET
    processor.vad.analyze_audio=quiet
    async def pair():
        for label,provider in zip(['A','B'],ordered):
            with wave.open(str(folder/f'0-{case}-{provider}.wav'),'rb') as src:pcm=src.readframes(src.getnframes())
            events.emit('paired_clip_start',case=case,label=label,provider=provider,byte_count=len(pcm),diagnostic_only=True)
            await processor.play(pcm,0)
            await asyncio.sleep(.35)
            events.emit('paired_clip_end',case=case,label=label,provider=provider)
            if label=='A':await asyncio.sleep(1.5) # Listening separation only, never production speech pacing.
        events.emit('paired_playback_complete',audibility='OPERATOR_REQUIRED');await runner.cancel()
    processor.controller.greet=lambda:processor.controller.spawn(pair())
    await runner.add_workers(worker)
    from speech_mcp.runtime import supervise
    reason=await supervise(runner,processor.controller,transport,events,asyncio.Event(),processor.pipeline_failed,100)
    if reason!='completed' or events.counts.get('paired_playback_complete')!=1:raise RuntimeError('paired_playback_incomplete')
def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--synthesize',action='store_true');parser.add_argument('--folder',required=True);parser.add_argument('--play-pair',choices=list(CORPUS));args=parser.parse_args()
    folder=(ROOT/'evidence/m5'/args.folder).resolve()
    if not folder.is_relative_to((ROOT/'evidence/m5').resolve()):raise SystemExit('invalid_folder')
    from loguru import logger
    logger.remove();logging.disable(logging.CRITICAL)
    if args.synthesize:folder.mkdir(exist_ok=False);asyncio.run(synthesize(folder))
    elif args.play_pair:
        events=Log(folder/('listening-'+args.play_pair+'-'+str(time.monotonic_ns())))
        try:asyncio.run(play_pair(folder,args.play_pair,events))
        finally:events.close()
    else:parser.error('select synthesis or a listening pair')
if __name__=='__main__':main()
