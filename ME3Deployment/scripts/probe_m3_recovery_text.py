"""One synthetic TEXT-only recovery check; never ASR/TTS or live evidence."""
import asyncio,json,sys,time
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
from speech_mcp.provider import GoogleSpeech
from voice_agent import EventLog

async def run(events):
    history=[{'type':'user_input','content':[{'type':'text','text':'Count slowly from one to ten.'}]},
      {'type':'model_output','control_state':'cancelled','content':[{'type':'text','text':'[UNSPOKEN CONTROL STATE] Previous response was cancelled. Do not resume that task; answer only the latest user request.'}]},
      {'type':'user_input','content':[{'type':'text','text':'Stop and tell me what two plus two is.'}]}]
    provider=GoogleSpeech();events.emit('synthetic_text_request',audio_uploaded=False,tts_requested=False,history_control='cancelled')
    try:answer=await provider.answer(history)
    finally:await provider.close()
    normalized=''.join(c.lower() for c in answer if c.isalnum())
    passed=normalized in {'four','4','twoplustwoisfour','twoplustwoequalsfour','2plus2is4','2plus2equals4'}
    events.emit('synthetic_text_result',passed=passed,answer_char_count=len(answer),answer_payload_retained=False,live_audio='NOT_TESTED')
    return passed

def main():
    load_dotenv(ROOT/'.env',encoding='utf-8-sig')
    events=EventLog(ROOT/'evidence/m3'/('recovery-text-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+str(time.monotonic_ns())))
    try:
        passed=asyncio.run(run(events));print(json.dumps({'synthetic_text_recovery':'PASS' if passed else 'FAIL','tts_requests':0,'live_audio':'NOT_TESTED'}));return 0 if passed else 2
    except Exception as exc:
        print(json.dumps({'status':'FAIL','error_type':type(exc).__name__,'tts_requests':0}));return 2
    finally:events.close()
if __name__=='__main__':raise SystemExit(main())
