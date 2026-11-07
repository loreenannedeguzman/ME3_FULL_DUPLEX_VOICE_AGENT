"""Thread-safe, bounded PCM playout and interrupted-response fencing."""
from collections import deque
import threading
import functools
import time


class PlayoutBuffer:
    def __init__(self, max_bytes):
        self.max_bytes = max_bytes
        self.epoch = 0
        self._chunks = deque()
        self._size = 0
        self._lock = threading.Lock()

    def offer(self, data, epoch):
        with self._lock:
            if epoch != self.epoch:
                return "stale"
            if self._size + len(data) > self.max_bytes:
                return "full"
            self._chunks.append(bytearray(data))
            self._size += len(data)
            return "accepted"

    def clear(self):
        with self._lock:
            discarded = self._size
            self.epoch += 1
            self._chunks.clear()
            self._size = 0
            return self.epoch, discarded

    def take(self, count):
        with self._lock:
            result = bytearray()
            while self._chunks and len(result) < count:
                chunk = self._chunks[0]
                size = min(count - len(result), len(chunk))
                result.extend(chunk[:size])
                del chunk[:size]
                self._size -= size
                if not chunk:
                    self._chunks.popleft()
            played = len(result)
            result.extend(bytes(count - played))
            return bytes(result), self.epoch, played, self._size


class ResponseFence:
    """Blocks late chunks after cancellation until a fresh response after speech-end."""
    def __init__(self):
        self.blocked = False
        self.user_speaking = False
        self.generation = 0
        self.dropped = 0

    def speech_start(self):
        self.user_speaking = True

    def speech_stop(self):
        self.user_speaking = False

    def interrupt(self):
        self.blocked = True

    def response_start(self):
        if self.user_speaking:
            self.blocked = True
            return False
        self.generation += 1
        self.blocked = False
        return True

    def accept_audio(self):
        if self.blocked:
            self.dropped += 1
            return False
        return True


class CaptureAudio:
    """Borrow the owned device; only an explicit normal input profile batches."""
    def __init__(self,audio,state):self.audio=audio;self.state=state
    def __getattr__(self,name):return getattr(self.audio,name)
    def open(self,**arguments):
        periods=self.state.input_callback_periods
        calibrator=self.state.input_clock_calibrator
        if periods==1 and calibrator is None:return self.audio.open(**arguments)
        if periods==1:
            arguments=dict(arguments,start=False)
        else:
            if periods!=4 or arguments.get('input') is not True or arguments.get('output') or arguments.get('rate')!=16000 or arguments.get('channels')!=1 or arguments.get('frames_per_buffer')!=320:raise ValueError('invalid_owned_capture_profile')
            arguments=dict(arguments,frames_per_buffer=320*periods,start=False)
        stream=self.audio.open(**arguments)
        try:
            if calibrator is not None:
                calibration=calibrator(stream)
                self.state.events.emit('input_clock_calibrated',**calibration)
            if periods==4:self.state.events.emit('input_device_open',rate=16000,channels=1,host_callback_ms=80,AEC_VAD_frame_ms=20,reported_input_latency_seconds=stream.get_input_latency(),source='PortAudio; not acoustic latency',original_samples_preserved=True)
        except BaseException:
            stream.close()
            raise
        return stream


class DuplexState:
    def __init__(self, settings, events):
        self.events = events
        self.rate = settings.output_rate
        self.channels = settings.output_channels
        self.buffer = PlayoutBuffer(int(self.rate * self.channels * 2 * 0.12))
        self.capture_count = 0
        self.last_capture_ns = None
        self.max_capture_gap_ns = 0
        self.callback_count = 0
        self.last_callback_epoch = -1
        self.writes = 0
        self.interruptions = 0
        self.loop = None
        self.output_callback_periods = 1
        self.input_callback_periods = 1
        self.input_clock_calibrator = None
        self.output_clock_calibrator = None

    def configure_input_callback(self,periods):
        if type(periods) is not int or periods not in (1,4) or self.loop is not None or self.capture_count:raise ValueError('invalid_input_callback_profile')
        self.input_callback_periods=periods

    def configure_output_callback(self, periods):
        if type(periods) is not int or periods not in (1, 3) or self.loop is not None or self.callback_count:
            raise ValueError("invalid_output_callback_profile")
        if periods == 3 and (self.rate != 48000 or self.channels != 2):
            raise ValueError("output_headroom_requires_48k_stereo")
        self.output_callback_periods = periods

    def post(self, event, **metadata):
        try:
            if self.loop and not self.loop.is_closed():
                self.loop.call_soon_threadsafe(functools.partial(self.events.emit, event, **metadata))
        except RuntimeError:
            pass

    def snapshot(self):
        return {
            "capture_callbacks": self.capture_count,
            "output_callbacks": self.callback_count,
            "writes": self.writes,
            "interruptions": self.interruptions,
            "max_capture_callback_gap_ns": self.max_capture_gap_ns,
            "epoch": self.buffer.epoch,
        }


def make_duplex_transport(settings, events):
    """Local input remains live; callback output never blocks a writer thread."""
    import asyncio
    import functools
    import pyaudio
    from pipecat.frames.frames import InterruptionFrame, StartFrame
    from pipecat.transports.base_output import BaseOutputTransport
    from pipecat.transports.base_transport import BaseTransport
    from pipecat.transports.local.audio import LocalAudioInputTransport, LocalAudioTransportParams

    state = DuplexState(settings, events)
    params = LocalAudioTransportParams(
        audio_in_enabled=True, audio_out_enabled=True,
        audio_out_channels=settings.output_channels,
        audio_out_10ms_chunks=2,
        input_device_index=settings.input_device,
        output_device_index=settings.output_device,
    )

    class LiveInput(LocalAudioInputTransport):
        async def setup(self, setup):
            state.loop=asyncio.get_running_loop()
            await super().setup(setup)

        def record_capture(self, status, now):
            if state.last_capture_ns is not None:
                state.max_capture_gap_ns = max(state.max_capture_gap_ns, now-state.last_capture_ns)
            state.last_capture_ns = now
            state.capture_count += 1
            if state.capture_count == 1 or state.capture_count % 10 == 0 or status:
                state.post("capture_callback", callback_ns=now,
                           capture_count=state.capture_count, status=status,
                           max_gap_ns=state.max_capture_gap_ns)
        def deliver_capture(self, in_data, frame_count, time_info, status):
            return super()._audio_in_callback(in_data, frame_count, time_info, status)

        def capture_ack(self):return None,pyaudio.paContinue

        def _audio_in_callback(self, in_data, frame_count, time_info, status):
            self.record_capture(status,time.monotonic_ns())
            return self.deliver_capture(in_data,frame_count,time_info,status)

    class CallbackOutput(BaseOutputTransport):
        def __init__(self, audio):
            super().__init__(params)
            self.audio = audio
            self.stream = None

        async def setup(self, setup):
            state.loop = asyncio.get_running_loop()
            await super().setup(setup)
            self.stream = self.audio.open(
                format=pyaudio.paInt16, channels=settings.output_channels,
                rate=self.sample_rate, output=True,
                output_device_index=settings.output_device,
                frames_per_buffer=self.sample_rate//50*state.output_callback_periods,
                stream_callback=self.callback, start=False,
            )
            try:
                if state.output_clock_calibrator is not None:
                    calibration=state.output_clock_calibrator(self.stream)
                    events.emit('output_clock_calibrated',**calibration)
            except BaseException:
                self.stream.close()
                self.stream=None
                raise
            events.emit("output_device_open", rate=self.sample_rate,
                        channels=settings.output_channels,
                        reported_output_latency_seconds=self.stream.get_output_latency(),
                        buffer_capacity_ms=120, callback_period_ms=20*state.output_callback_periods,
                        source="PortAudio; not acoustic")

        def callback(self, in_data, frame_count, time_info, status):
            now = time.monotonic_ns()
            data, epoch, played, queued = state.buffer.take(
                frame_count*settings.output_channels*2
            )
            state.callback_count += 1
            changed = epoch != state.last_callback_epoch
            state.last_callback_epoch = epoch
            if played or changed or status:
                state.post("playout_callback", callback_ns=now, epoch=epoch,
                           played_bytes=played, queued_bytes=queued,
                           capture_count=state.capture_count, status=status,
                           callback_count=state.callback_count)
            if changed:
                state.post("callback_epoch_observed", callback_ns=now, epoch=epoch,
                           capture_count=state.capture_count,
                           note="callback submissions changed; not physical acoustic stop")
            return data, pyaudio.paContinue

        async def start(self, frame: StartFrame):
            await super().start(frame)
            self.stream.start_stream()
            await self.set_transport_ready(frame)

        async def process_frame(self, frame, direction):
            if isinstance(frame, InterruptionFrame):
                epoch, discarded = state.buffer.clear()
                state.interruptions += 1
                events.emit("playout_clear", interruption_frame_id=frame.id,
                            epoch=epoch, discarded_bytes=discarded,
                            capture_count=state.capture_count,
                            last_capture_ns=state.last_capture_ns)
                await super().process_frame(frame, direction)
                events.emit("pipeline_output_reset", epoch=epoch,
                            capture_count=state.capture_count)
            else:
                await super().process_frame(frame, direction)

        async def write_audio_frame(self, frame):
            epoch = state.buffer.epoch
            while self.stream is not None:
                result = state.buffer.offer(frame.audio, epoch)
                if result == "stale":
                    events.emit("stale_writer_dropped", epoch=epoch,
                                current_epoch=state.buffer.epoch)
                    return False
                if result == "accepted":
                    state.writes += 1
                    return True
                await asyncio.sleep(0.005)
            return False

        async def cleanup(self):
            try:
                await super().cleanup()
            finally:
                state.buffer.clear()
                if self.stream is not None:
                    self.stream.stop_stream()
                    self.stream.close()
                    self.stream = None
                events.emit("duplex_transport_closed", **state.snapshot())

    class DuplexTransport(BaseTransport):
        def __init__(self):
            super().__init__()
            self.audio = pyaudio.PyAudio()
            self.state = state
            self._input = LiveInput(CaptureAudio(self.audio,state), params)
            self._output = CallbackOutput(self.audio)
            self._closed = False

        def input(self):
            return self._input

        def output(self):
            return self._output

        async def close(self):
            if not self._closed:
                self.audio.terminate()
                self._closed = True

    return DuplexTransport(), state


def make_response_fence(events):
    from pipecat.frames.frames import (
        InterruptionFrame, LLMFullResponseStartFrame, OutputAudioRawFrame,
        UserStartedSpeakingFrame, UserStoppedSpeakingFrame,
        VADUserStartedSpeakingFrame, VADUserStoppedSpeakingFrame,
    )
    from pipecat.processors.frame_processor import FrameProcessor, FrameDirection

    class FenceProcessor(FrameProcessor):
        def __init__(self):
            super().__init__()
            self.fence = ResponseFence()

        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if direction == FrameDirection.DOWNSTREAM:
                if isinstance(frame, (VADUserStartedSpeakingFrame, UserStartedSpeakingFrame)):
                    self.fence.speech_start()
                    events.emit("local_speech_start", frame_type=type(frame).__name__)
                elif isinstance(frame, (VADUserStoppedSpeakingFrame, UserStoppedSpeakingFrame)):
                    self.fence.speech_stop()
                    events.emit("local_speech_stop", frame_type=type(frame).__name__)
                elif isinstance(frame, InterruptionFrame):
                    self.fence.interrupt()
                    events.emit("response_fenced", generation=self.fence.generation,
                                interruption_frame_id=frame.id)
                elif isinstance(frame, LLMFullResponseStartFrame):
                    accepted = self.fence.response_start()
                    events.emit("response_generation", generation=self.fence.generation,
                                admitted=accepted, user_speaking=self.fence.user_speaking)
                elif isinstance(frame, OutputAudioRawFrame) and not self.fence.accept_audio():
                    events.emit("stale_audio_dropped", generation=self.fence.generation,
                                byte_count=len(frame.audio))
                    return
            await self.push_frame(frame, direction)

    return FenceProcessor()
