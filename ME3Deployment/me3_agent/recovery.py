"""Settle expired output before its recovery notice; never clear a newer turn."""
import asyncio

def attach(processor,events):
    async def invalidate(generation):
        if not processor.controller.current(generation):return False
        probe=processor.probe
        processor.probe=None;processor.probing=False
        if probe is not None and not probe.done():
            probe.cancel()
            await asyncio.gather(probe,return_exceptions=True)
        # Probe cleanup may yield to a new user turn. Preserve that turn's output.
        if not processor.controller.current(generation):return False
        await processor.clear_output('expired_response')
        events.emit('agent_expired_output_cleared',generation=generation,probe_settled=True,physical_acoustic_stop_measured=False)
        return processor.controller.current(generation)
    processor.controller.invalidate_timed_out_output=invalidate
