"""One bounded text-only compatibility check, not speech acceptance."""
import asyncio,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT/'.env',encoding='utf-8-sig')
from speech_mcp.provider import GoogleSpeech,TEXT_MODEL
async def main():
    p=GoogleSpeech()
    try:
        result=await p.answer([{'type':'user_input','content':[{'type':'text','text':'The test word is mango. Please remember it. Reply briefly.'}]}])
        print(json.dumps({'text_model':TEXT_MODEL,'nonempty_reply':bool(result),'reply_chars':len(result),'audio_sent':False,'live_conversation':'NOT_TESTED'}))
    except Exception as exc:
        print(json.dumps({'status':'FAIL','type':type(exc).__name__,'code':getattr(exc,'code',None)}));return 2
    finally:await p.close()
    return 0
if __name__=='__main__':raise SystemExit(asyncio.run(main()))
