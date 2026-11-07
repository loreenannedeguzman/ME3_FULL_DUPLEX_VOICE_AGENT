"""M4: unchanged accepted M3 speech pipeline plus confirmed, independently verified task MCP."""
import argparse,asyncio,json,logging,os,platform,sys,time
from datetime import datetime,timezone
from dotenv import load_dotenv
import mcp_voice_agent as m3
from voice_agent import ROOT,Settings,configure_pi_audio
from speech_mcp.client import SpeechClients
from speech_mcp.provider import GoogleSpeech
from speech_mcp.runtime import supervise,SessionFailure
from speech_mcp.diagnostics import StatusEventLog
from task_mcp.client import TaskClient
from task_mcp.reasoner import TaskReasoner

async def run(settings,events,build_only=False):
    if build_only:
        await m3.run(settings,events,build_only=True)
        events.emit('m4_offline_build',speech_source='unchanged mcp_voice_agent.build_pipeline',task_writes=0);return
    ordinary=GoogleSpeech();deferred=None
    try:
        async with TaskClient(events) as tasks:
            async with SpeechClients(events) as speech:
                reasoner=TaskReasoner(ordinary,tasks,events)
                runner,worker,transport,processor=m3.build_pipeline(settings,events,speech,reasoner)
                reasoner.controller=processor.controller
                greeting='I can save an item to your Pi shopping list. To begin, say add to my shopping list.'
                processor.controller.greet=lambda:processor.controller.spawn(processor.controller.speak(greeting,0,'normal',greeting=True))
                await runner.add_workers(worker)
                events.emit('m4_configuration',task='persistent Pi shopping list',task_transport='stdio MCP',task_write_budget=1,task_rpc_budget=20,task_timeout_seconds=5,verification='fresh readonly SQLite connection, plus independent operator/SSH check',speech_source='unchanged accepted M3 build_pipeline')
                events.emit('session_start',host=platform.system(),machine=platform.machine(),max_seconds=settings.max_seconds,speech_path='asr_mcp-task_or_text-tts_mcp')
                async def stop_failed_task():
                    await reasoner.failed.wait()
                    try:
                        async with asyncio.timeout(25):
                            while processor.controller.task is not None and not processor.controller.task.done():await asyncio.sleep(.05)
                        await asyncio.sleep(.3)
                    finally:await runner.cancel()
                task_stop=asyncio.create_task(stop_failed_task());reason='startup'
                try:
                    reason=await supervise(runner,processor.controller,transport,events,speech.limit_reached,processor.pipeline_failed,settings.max_seconds,service_failed=speech.failure)
                except BaseException as exc:
                    deferred=exc;reason='caller_cancelled' if isinstance(exc,asyncio.CancelledError) else 'worker_or_cleanup_error'
                finally:
                    task_stop.cancel();await asyncio.gather(task_stop,return_exceptions=True)
                    events.emit('session_end',input_frames=events.input_frames,output_frames=events.output_frames,human_turns=processor.controller.turns,error_events=events.counts.get('turn_error',0),pipeline_faults=events.counts.get('pipeline_fault',0),task_failures=events.counts.get('task_failure',0),task_rpc_count=tasks.calls,stop_reason=reason)
                if deferred is None and (reason in {'provider_quota','pipeline_fault','runner_cancelled','speech_service_error'} or events.error_seen or not events.output_frames or events.counts.get('turn_error',0) or reasoner.blocked):deferred=SessionFailure('m4_incomplete_or_failed')
        if deferred is not None:raise deferred
    finally:await ordinary.close()

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--build-check',action='store_true');args=p.parse_args()
    load_dotenv(ROOT/'.env',encoding='utf-8-sig')
    from loguru import logger
    logger.remove();logging.disable(logging.CRITICAL);events=None
    try:
        settings=Settings.load(live=not args.build_check);configure_pi_audio(settings)
        events=StatusEventLog(ROOT/'evidence/m4'/('runtime-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+str(time.monotonic_ns())))
        asyncio.run(run(settings,events,args.build_check));return 0
    except KeyboardInterrupt:return 130
    except Exception as exc:
        print(json.dumps({'status':'FAIL','error_type':type(exc).__name__}),file=sys.stderr);return 2
    finally:
        if events:events.close()
if __name__=='__main__':raise SystemExit(main())
