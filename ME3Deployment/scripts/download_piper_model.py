"""Public fixed model download with explicit bounded resumption and SHA verification. No API keys."""
from pathlib import Path
import concurrent.futures,hashlib,json,time,urllib.request,urllib.error,sys
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1]
BASE='https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ljspeech/high/'
SIZE=114199011;SHA='5d4f08ba6a2a48c44592eed3ce56bf85e9de3dd4e20df90541ae68a8310c029a';PART=4*1024*1024

def main():
    started=time.monotonic();deadline=started+600;work=ROOT/'.tmp/piper-download';work.mkdir(parents=True,exist_ok=True)
    dest=ROOT/'models/piper';dest.mkdir(parents=True,exist_ok=True);events=ROOT/'evidence/m5/model-range-download-002.jsonl'
    log=events.open('x');records=[]
    def emit(value):log.write(json.dumps({'utc':datetime.now(timezone.utc).isoformat(),**value})+'\n');log.flush()
    def fetch(spec):
        index,start,end=spec;path=work/f'part-{index:03d}';length=end-start+1
        if path.exists() and path.stat().st_size>length:return {'part':index,'ok':False,'reason':'oversized_existing_part'}
        for attempt in range(1,4):
            have=path.stat().st_size if path.exists() else 0
            if have==length:return {'part':index,'ok':True,'bytes':have}
            if time.monotonic()>deadline:return {'part':index,'ok':False,'reason':'overall600s_limit'}
            offset=start+have;url=BASE+'en_US-ljspeech-high.onnx?download=true&me3range='+str(offset)+'-'+str(time.monotonic_ns())
            try:
                req=urllib.request.Request(url,headers={'Range':f'bytes={offset}-{end}','Cache-Control':'no-cache'})
                with urllib.request.urlopen(req,timeout=20) as response:
                    actual=response.headers.get('Content-Range')
                    if response.status!=206 or actual!=f'bytes {offset}-{end}/{SIZE}':return {'part':index,'ok':False,'reason':'range_mismatch','status':response.status,'actual_range':actual,'expected_range':f'bytes {offset}-{end}/{SIZE}'}
                    with path.open('ab') as out:
                        while chunk:=response.read(65536):
                            if time.monotonic()>deadline:raise TimeoutError('overall_time_limit')
                            if out.tell()+len(chunk)>length:raise ValueError('range_size_limit')
                            out.write(chunk)
                if path.stat().st_size==length:return {'part':index,'ok':True,'bytes':length,'attempts':attempt}
                emit({'part':index,'attempt':attempt,'reason':'truncated_response','retained_bytes':path.stat().st_size})
            except Exception as exc:
                emit({'part':index,'attempt':attempt,'error_type':type(exc).__name__,'http_code':getattr(exc,'code',None),'retained_bytes':path.stat().st_size if path.exists() else 0})
                if isinstance(exc,(ValueError,urllib.error.HTTPError)):return {'part':index,'ok':False,'reason':type(exc).__name__,'http_code':getattr(exc,'code',None)}
        return {'part':index,'ok':False,'reason':'three_explicit_download_attempts_exhausted'}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for row in pool.map(fetch,[(i,s,min(SIZE-1,s+PART-1)) for i,s in enumerate(range(0,SIZE,PART))]):records.append(row);emit(row)
    code=2
    if all(x['ok'] for x in records):
        assembled=work/'assembled-002.onnx';digest=hashlib.sha256();count=0
        with assembled.open('xb') as out:
            for i in range(len(records)):
                with (work/f'part-{i:03d}').open('rb') as src:
                    while chunk:=src.read(1024*1024):digest.update(chunk);out.write(chunk);count+=len(chunk)
        if count==SIZE and digest.hexdigest()==SHA:
            target=dest/'en_US-ljspeech-high.onnx'
            if target.exists():raise RuntimeError('preserve_existing_model')
            assembled.rename(target);files={}
            for name in ['MODEL_CARD','en_US-ljspeech-high.onnx.json','en_US-ljspeech-high.onnx']:
                p=dest/name
                if not p.exists():
                    with urllib.request.urlopen(BASE+name,timeout=20) as response:p.write_bytes(response.read(128*1024+1))
                    if p.stat().st_size>128*1024:raise RuntimeError('metadata_size_limit')
                files[name]={'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'source_url':BASE+name}
            manifest=ROOT/'evidence/m5/piper-model-001.json'
            with manifest.open('x') as out:out.write(json.dumps({'model':'en_US-ljspeech-high','records':files,'verified_remote_sha256':SHA,'network_scope':'Public model only;explicit bounded resumptions,no TTS API request'},indent=2))
            code=0
        else:emit({'reason':'full_model_sha256_mismatch','actual_sha256':digest.hexdigest()})
    emit({'status':'PASS' if code==0 else 'FAIL','exit_code':code,'seconds':time.monotonic()-started,'request_policy':'4workers/3explicit attempts per part/600s wall plus socket cleanup','no_tts_api_calls':True});log.close();return code
if __name__=='__main__':raise SystemExit(main())
