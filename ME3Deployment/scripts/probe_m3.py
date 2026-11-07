"""Actual MCP protocol and optional synthetic speech check. Never a live test."""
import argparse,asyncio,base64,json,os,sys,time
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
from voice_agent import EventLog
from speech_mcp.client import SpeechClients
from speech_mcp.provider import GoogleSpeech

async def run(live,events,short=False):
    async with SpeechClients(events) as clients:
        for role,arguments in [('asr',{'pcm_b64':'not base64','generation':1,'request_id':'invalid-audio','sample_rate':16000}),('tts',{'text':'','generation':1,'request_id':'invalid-text'})]:
            tool='transcribe' if role=='asr' else 'synthesize'
            data=clients.unpack(await clients.sessions[role].call_tool(tool,arguments))
            if data.get('ok') or data.get('error') not in {'invalid_base64','invalid_text'}:raise ValueError('invalid_input_not_rejected')
            events.emit('mcp_invalid_input_rejected',role=role,error=data['error'],provider_called=False)
        if live:
            phrase='Mango.' if short else 'The test word is mango.'
            result=await clients.call('tts',1,text=phrase)
            # TTS24kHz must be resampled, not mislabeled as16kHz.
            import audioop
            pcm=base64.b64decode(result['pcm_b64']);pcm,_=audioop.ratecv(pcm,2,1,24000,16000,None)
            recognized=await clients.call('asr',1,pcm_b64=base64.b64encode(pcm).decode(),sample_rate=16000)
            normalized=lambda x:''.join(c.lower() for c in x if c.isalnum())
            matched=normalized(recognized['text'])==normalized(phrase)
            provider=GoogleSpeech()
            try: answer=await provider.answer([{'type':'user_input','content':[{'type':'text','text':'What is two plus two? Answer in one word.'}]}])
            finally:await provider.close()
            events.emit('synthetic_roundtrip_result',exact_normalized_match=matched,asr_chars=len(recognized['text']),tts_bytes=len(pcm),text_reasoning_answer_four='four' in answer.lower() or '4' in answer,live_microphone=False,audibility_verified=False)
            if not matched or not ('four' in answer.lower() or '4' in answer):raise ValueError('synthetic_roundtrip_mismatch')
    print(json.dumps({'mcp_protocol':'PASS','invalid_inputs':'PASS','synthetic_provider_roundtrip':'PASS' if live else 'NOT_RUN','live_conversation':'NOT_TESTED','audibility':'NOT_VERIFIED'}))

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--provider-roundtrip',action='store_true');p.add_argument('--short-answer',action='store_true');args=p.parse_args()
    load_dotenv(ROOT/'.env',encoding='utf-8-sig')
    events=EventLog(ROOT/'evidence/m3'/('probe-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+str(time.monotonic_ns())))
    try:asyncio.run(run(args.provider_roundtrip,events,args.short_answer))
    except Exception as exc:
        print(json.dumps({'status':'FAIL','error_type':type(exc).__name__}));return 2
    finally:events.close()
    return 0
if __name__=='__main__':raise SystemExit(main())
