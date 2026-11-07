"""Ordered worker supervision independent of any provider/device implementation."""
import asyncio

class SessionFailure(RuntimeError):
    """Metadata names the failed stage; no provider message is retained here."""

async def supervise(runner,controller,transport,events,quota,failed,seconds,shutdown_seconds=5,service_failed=None,stop_requested=None):
    running=asyncio.create_task(runner.run())
    quota_watch=asyncio.create_task(quota.wait())
    fault_watch=asyncio.create_task(failed.wait())
    watchers=[quota_watch,fault_watch]
    if stop_requested is not None:watchers.append(asyncio.create_task(stop_requested.wait()))
    if service_failed is not None:watchers.append(asyncio.create_task(service_failed.wait()))
    reason='completed';problem=None;cleanup_problem=None
    try:
        done,_=await asyncio.wait({running,*watchers},timeout=seconds,return_when=asyncio.FIRST_COMPLETED)
        if failed.is_set():reason='pipeline_fault'
        elif quota.is_set():reason='provider_quota'
        elif service_failed is not None and service_failed.is_set():reason='speech_service_error'
        elif stop_requested is not None and stop_requested.is_set():reason='caller_cancelled'
        elif running not in done:reason='time_limit'
        elif running.cancelled():reason='runner_cancelled'
        elif running.exception() is not None:
            reason='runner_error';problem=running.exception()
        if reason=='provider_quota':events.emit('session_provider_limited')
    except BaseException as exc:
        problem=exc;reason='caller_cancelled' if isinstance(exc,asyncio.CancelledError) else 'supervisor_error'
    finally:
        for watcher in watchers:watcher.cancel()
        await asyncio.gather(*watchers,return_exceptions=True)
        try:await controller.close()
        except Exception as exc:cleanup_problem=exc
        try:
            if not running.done():await runner.cancel()
            done,_=await asyncio.wait({running},timeout=shutdown_seconds)
            if not done:
                events.emit('worker_shutdown_timeout')
                running.cancel()
                done,_=await asyncio.wait({running},timeout=1)
                cleanup_problem=SessionFailure('worker_shutdown_timeout')
            if done and not running.cancelled() and running.exception() is not None and problem is None:
                problem=running.exception();reason='runner_error'
        except Exception as exc:cleanup_problem=exc
        if running.done():
            try:await transport.close()
            except Exception as exc:cleanup_problem=exc
        else:
            events.emit('transport_close_deferred',reason='worker_still_active')
            cleanup_problem=SessionFailure('worker_did_not_stop')
        if cleanup_problem:
            events.emit('session_cleanup_error',error_type=type(cleanup_problem).__name__)
        events.emit('session_stop',reason=reason,worker_finished=running.done(),cleanup_ok=cleanup_problem is None)
    if problem is not None:raise problem
    if cleanup_problem is not None:raise cleanup_problem
    return reason
