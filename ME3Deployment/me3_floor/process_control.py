import asyncio,os,signal
def need(v,c):
    if not v:raise ValueError(c)

async def settle(process,job=None):
    """No retry or respawn; bounded cleanup of a started POSIX process group."""
    if process is None:return True
    if process.returncode is None:
        try:os.killpg(process.pid,signal.SIGTERM)
        except ProcessLookupError:pass
        wait=asyncio.create_task(process.wait());done,pending=await asyncio.wait({wait},timeout=2)
        if pending:
            try:os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            done,pending=await asyncio.wait({wait},timeout=2)
        if pending:return False
    if job is not None and not job.done():
        job.cancel();done,pending=await asyncio.wait({job},timeout=1)
        if pending:return False
    return process.returncode is not None

async def spawn_bounded(factory,track):
    """Bound creation; retain any late result for failclosed cleanup."""
    job=asyncio.create_task(factory());track.append(job)
    done,pending=await asyncio.wait({job},timeout=10)
    if pending:
        job.cancel();done,pending=await asyncio.wait({job},timeout=1)
        if pending:
            def late(value):
                if not value.cancelled() and value.exception() is None:
                    process=value.result()
                    if process.returncode is None:
                        try:os.killpg(process.pid,signal.SIGKILL)
                        except ProcessLookupError:pass
            job.add_done_callback(late)
        raise ValueError('process_creation_deadline')
    return job.result()
