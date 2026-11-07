"""Model metadata only; no generation, audio upload or billing change."""
from pathlib import Path
import sys,json
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1]/'.env',encoding='utf-8-sig')
import os
from google import genai
from speech_mcp.provider import ASR_MODEL,TTS_MODEL,TEXT_MODEL
client=genai.Client(api_key=os.environ['GOOGLE_API_KEY'])
failed=False
try:
    for name in sorted({ASR_MODEL,TTS_MODEL,TEXT_MODEL}):
        try:
            m=client.models.get(model=name)
            print(json.dumps({'model':m.name,'accessible':True,'generation_requested':False,'audio_uploaded':False}))
        except Exception as exc:
            failed=True;print(json.dumps({'model':name,'accessible':False,'type':type(exc).__name__,'code':getattr(exc,'code',None)}))
finally:client.close()
raise SystemExit(2 if failed else 0)
