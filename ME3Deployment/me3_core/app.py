import argparse,asyncio,json,logging,os,sys,time
from pathlib import Path
from dotenv import load_dotenv
from voice_agent import Settings,configure_pi_audio
from me3_core.events import CoreEvents
from me3_core.integration import apply
ROOT=Path(__file__).resolve().parents[1]
def main():
    p=argparse.ArgumentParser();p.add_argument('--build-check',action='store_true');a=p.parse_args()
    load_dotenv(ROOT/'.env',encoding='utf-8-sig');os.environ.update(TTS_PROVIDER='piper',PIPER_WORKER_MODE='resident',PIPER_PYTHON=str(ROOT/('.venv-piper/bin/python' if sys.platform=='linux' else '.venv/Scripts/python.exe')),PIPER_MODEL=str(ROOT/'models/piper/en_US-ljspeech-high.onnx'))
    from loguru import logger
    logger.remove();logging.disable(logging.CRITICAL)
    events=CoreEvents(ROOT/'evidence/core'/('runtime-'+str(time.monotonic_ns())))
    try:
        app=apply(events);settings=Settings.load(live=not a.build_check);configure_pi_audio(settings);asyncio.run(app.run(settings,events,a.build_check));return 0
    except KeyboardInterrupt:return 130
    except Exception as exc:print(json.dumps({'status':'FAIL','error_type':type(exc).__name__}));return 2
    finally:events.close()
if __name__=='__main__':raise SystemExit(main())
