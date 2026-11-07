"""Lossless bounded provider payload encoding; no resampling, files, keys or shell."""
import asyncio,os,shutil
import anyio
from speech_mcp.contracts import pcm_to_wav
async def run_ffmpeg(data,args):
    binary=shutil.which('ffmpeg')
    if not binary:raise ValueError('existing_ffmpeg_required')
    env={k:v for k,v in os.environ.items() if k in {'PATH','SYSTEMROOT','LD_LIBRARY_PATH','HOME','TMPDIR','TMP','TEMP'}}
    start=asyncio.create_task(asyncio.create_subprocess_exec(binary,'-nostdin','-hide_banner','-loglevel','error',*args,stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,env=env));process=None
    try:
        async with asyncio.timeout(3):
            process=await asyncio.shield(start);out,_=await process.communicate(data)
        if process.returncode!=0 or len(out)>550000:raise ValueError('audio_codec_failed_or_excess_output')
        return out
    finally:
        with anyio.move_on_after(2,shield=True) as cleanup_scope:
            if process is None:process=await start
            if process.returncode is None:
                try:process.terminate()
                except ProcessLookupError:pass
                try:await asyncio.wait_for(process.wait(),.5)
                except TimeoutError:process.kill();await asyncio.wait_for(process.wait(),1)
        if cleanup_scope.cancel_called:
            def late_reap(future):
                if future.cancelled() or future.exception() is not None:return
                child=future.result()
                if child.returncode is None:
                    try:child.kill()
                    except ProcessLookupError:pass
                    asyncio.create_task(child.wait())
            start.add_done_callback(late_reap)
            raise ValueError('codec_cleanup_deadline')
async def encode(pcm,format):
    if not isinstance(pcm,bytes) or not 0<len(pcm)<=480000 or len(pcm)%2:raise ValueError('invalid_codec_pcm')
    if format=='wav':return pcm_to_wav(pcm),'audio/wav'
    if format!='flac':raise ValueError('explicit_audio_format_required')
    out=await run_ffmpeg(pcm,['-f','s16le','-ar','16000','-ac','1','-i','pipe:0','-map_metadata','-1','-c:a','flac','-compression_level','5','-f','flac','pipe:1'])
    if not out.startswith(b'fLaC'):raise ValueError('invalid_flac_header')
    return out,'audio/flac'
async def decode(flac):return await run_ffmpeg(flac,['-f','flac','-i','pipe:0','-f','s16le','-acodec','pcm_s16le','pipe:1'])
