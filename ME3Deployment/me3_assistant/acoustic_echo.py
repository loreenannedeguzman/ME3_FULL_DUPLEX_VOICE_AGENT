"""Opt-in, local reference-based AEC; no transcript matching or microphone gating.

The opt-in speaker runtime uses WebRTC APM; retained Speex classes support
revision-specific diagnostics. Both process20ms mono PCM16 at16kHz. The reference
is the actual final output callback PCM (including music and cancellation
silence), aligned using ADC/DAC timestamps mapped to the callback clock.
Only short in-memory PCM buffers exist; no audio is recorded or transmitted here.
"""
import array
import ctypes
import ctypes.util
import math
import sys
import threading
import time
from collections import deque

RATE = 16000
SAMPLES = 320
FRAME_BYTES = SAMPLES * 2
OUTPUT_UNDERFLOW = 4  # PortAudio paOutputUnderflow, not an input flag.
IDLE_SECONDS = 1.0  # Five times the echo filter's tail; digital silence only.
MAX_IDLE_UNDERFLOWS = 2
MAX_IDLE_SILENCE_GAP_SECONDS = .25  # Only bounded, already-silent underflows.
IDLE_UNDERFLOW_WINDOW_SECONDS = 1.0  # Rate bound, not a session-lifetime limit.
FAULT_CODES = frozenset((
    'speaker_aec_capture_queue_overrun',
    'speaker_aec_capture_discontinuity', 'speaker_aec_playback_discontinuity',
    'speaker_aec_playback_clock_reversed', 'speaker_aec_timestamp_invalid',
    'speaker_aec_timestamp_out_of_range', 'speaker_aec_requires_20ms_48k_stereo',
    'speaker_aec_resampler_frame_invalid', 'speaker_aec_requires_20ms_16k_mono',
    'speaker_aec_output_frame_invalid', 'speaker_aec_closed',
    'speaker_aec_idle_underflow_limit',
))


def audio_mode(grant):
    mode = grant.get('audio_mode', 'earphones')
    if mode not in ('earphones', 'speaker_aec'):
        raise ValueError('unsupported_assistant_audio_mode')
    return mode


class SpeexEcho:
    """Native state is used only by the capture callback; close is serialized."""
    def __init__(self):
        name = ctypes.util.find_library('speexdsp')
        if not name:
            raise ValueError('speaker_aec_requires_installed_speexdsp')
        self.lib = ctypes.CDLL(name)
        pointer = ctypes.POINTER(ctypes.c_int16)
        self.lib.speex_echo_state_init.argtypes = [ctypes.c_int, ctypes.c_int]
        self.lib.speex_echo_state_init.restype = ctypes.c_void_p
        self.lib.speex_echo_ctl.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        self.lib.speex_echo_ctl.restype = ctypes.c_int
        self.lib.speex_echo_cancellation.argtypes = [ctypes.c_void_p, pointer, pointer, pointer]
        self.lib.speex_echo_cancellation.restype = None
        self.lib.speex_echo_state_destroy.argtypes = [ctypes.c_void_p]
        self.lib.speex_echo_state_destroy.restype = None
        self.state = self.lib.speex_echo_state_init(SAMPLES, RATE // 5)
        if not self.state:
            raise ValueError('speaker_aec_allocation_failed')
        rate = ctypes.c_int(RATE)
        if self.lib.speex_echo_ctl(self.state, 24, ctypes.byref(rate)) != 0:
            self.close()
            raise ValueError('speaker_aec_rate_rejected')
        self.vector = ctypes.c_int16 * SAMPLES

    def process(self, microphone, reference):
        if not self.state or len(microphone) != FRAME_BYTES or len(reference) != FRAME_BYTES:
            raise ValueError('speaker_aec_frame_invalid')
        # Pi and supported Windows hosts are little-endian; reject other hosts.
        if sys.byteorder != 'little':
            raise ValueError('speaker_aec_requires_little_endian')
        out = self.vector()
        self.lib.speex_echo_cancellation(self.state,
            self.vector.from_buffer_copy(microphone), self.vector.from_buffer_copy(reference), out)
        return bytes(out)

    def close(self):
        if self.state:
            self.lib.speex_echo_state_destroy(self.state)
            self.state = None


class ResidualEcho(SpeexEcho):
    """Documented residual-echo gains; adds one 20ms preprocessing frame.

Noise floor is 0dB, AGC/VAD/dereverb disabled. Denoise must stay enabled
because Speex otherwise bypasses *all* spectral gains, including echo gains.
"""
    def __init__(self):
        self.pre = None
        super().__init__()
        lib = self.lib
        lib.speex_preprocess_state_init.argtypes = [ctypes.c_int, ctypes.c_int]
        lib.speex_preprocess_state_init.restype = ctypes.c_void_p
        lib.speex_preprocess_ctl.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        lib.speex_preprocess_ctl.restype = ctypes.c_int
        lib.speex_preprocess_run.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int16)]
        lib.speex_preprocess_run.restype = ctypes.c_int
        lib.speex_preprocess_state_destroy.argtypes = [ctypes.c_void_p]
        lib.speex_preprocess_state_destroy.restype = None
        self.pre = lib.speex_preprocess_state_init(SAMPLES, RATE)
        if not self.pre:
            self.close()
            raise ValueError('speaker_aec_preprocess_allocation_failed')
        # Official SPEEX_PREPROCESS_SET_* controls: DENOISE, AGC, VAD,
        # DEREVERB, NOISE_SUPPRESS. Echo attenuation retains library defaults.
        for control, value in ((0, 1), (2, 0), (4, 0), (8, 0), (18, 0)):
            number = ctypes.c_int(value)
            if lib.speex_preprocess_ctl(self.pre, control, ctypes.byref(number)) != 0:
                self.close()
                raise ValueError('speaker_aec_preprocess_control_rejected')
        if lib.speex_preprocess_ctl(self.pre, 24, self.state) != 0:
            self.close()
            raise ValueError('speaker_aec_preprocess_echo_binding_failed')

    def process(self, microphone, reference):
        result = self.vector.from_buffer_copy(super().process(microphone, reference))
        self.lib.speex_preprocess_run(self.pre, result)
        return bytes(result)

    def close(self):
        if self.pre:
            self.lib.speex_preprocess_state_destroy(self.pre)
            self.pre = None
        super().close()


class CausalEcho:
    """One20ms microphone frame gives the current reference a causal lead.

    This uniform buffer preserves near-end speech instead of gating capture
    during playback. Residual preprocessing adds its own20ms: total40ms.
    """
    def __init__(self, engine=None):
        self.engine = engine if engine is not None else ResidualEcho()
        self.previous = bytes(FRAME_BYTES)
        self.closed = False

    def process(self, microphone, reference):
        if self.closed:
            raise ValueError('speaker_aec_closed')
        if len(microphone) != FRAME_BYTES or len(reference) != FRAME_BYTES:
            raise ValueError('speaker_aec_frame_invalid')
        result = self.engine.process(self.previous, reference)
        self.previous = bytes(microphone)
        return result

    def close(self):
        if not self.closed:
            self.closed = True
            self.previous = bytes(FRAME_BYTES)
            self.engine.close()



def speaker_echo():
    # Explicit opt-in speaker path only. Missing/invalid native artifact fails
    # before MCP/models/audio; never fall back to unfiltered capture or Speex.
    from me3_assistant.webrtc_echo import WebRtcEcho
    return EchoControl(engine=WebRtcEcho())


def callback_time(info, field, now):
    # Each stream may have its own time origin. Use its sample-current offset.
    current = info.get('current_time')
    sample = info.get(field)
    if any(type(x) not in (int, float) or not math.isfinite(x) for x in (current, sample)):
        raise ValueError('speaker_aec_timestamp_invalid')
    delta = sample - current
    if abs(delta) > .5:
        raise ValueError('speaker_aec_timestamp_out_of_range')
    return now + delta


class StreamClockMapper:
    """Fixed stream-to-monotonic translation; one instance per stream.

    Bind once outside callbacks. map() has no device calls or status bypass.
    Calls on one instance must be serialized by the integration.
    """

    def __init__(self):
        self._binding = None
        self._last_current = None
        self._last_sample = None

    @staticmethod
    def _finite(value):
        if isinstance(value, (str, bytes, bool)):
            raise ValueError("expected a finite numeric timestamp")
        try:
            value = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("expected a finite numeric timestamp") from exc
        if not math.isfinite(value):
            raise ValueError("expected a finite numeric timestamp")
        return value

    @property
    def uncertainty_seconds(self):
        """Read-only half-width of the accepted binding bracket."""
        if self._binding is None:
            raise ValueError("mapper is unbound")
        return self._binding[2]

    def bind(self, raw_current, monotonic_before, monotonic_after):
        """Bind exactly once to an externally measured <= 5 ms bracket."""
        if self._binding is not None:
            raise ValueError("mapper is already bound")
        before = self._finite(monotonic_before)
        after = self._finite(monotonic_after)
        width = after - before
        # Reject the bracket before processing raw_current; never retry here.
        if not 0.0 <= width <= 0.005:
            raise ValueError("binding bracket must be ordered and <= 0.005 s")
        raw = self._finite(raw_current)
        uncertainty = width / 2.0
        self._binding = (raw, before + uncertainty, uncertainty)
        self._last_current = raw
        return self

    def map(self, raw_current, raw_sample):
        """Translate a sample start, committing progression only on success."""
        if self._binding is None:
            raise ValueError("mapper is unbound")
        current = self._finite(raw_current)
        sample = self._finite(raw_sample)
        if abs(sample - current) > 0.5:
            raise ValueError("sample/current delta exceeds 0.5 s")
        if current < self._last_current:
            raise ValueError("raw current moved backward")
        if self._last_sample is not None and sample < self._last_sample:
            raise ValueError("raw sample start moved backward")
        raw_origin, monotonic_origin, _ = self._binding
        mapped = monotonic_origin + (sample - raw_origin)
        if not math.isfinite(mapped):
            raise ValueError("mapped timestamp is not finite")
        self._last_current = current
        self._last_sample = sample
        return mapped



class StreamClock:
    """One outside-callback calibration; later callback arrival is not media time."""
    def __init__(self, field):
        self.field = field
        self.mapper = None
        self.last_current = self.last_sample = None
        self.calibration = None

    def calibrate(self, stream):
        if self.mapper is not None:
            raise ValueError('speaker_aec_timestamp_invalid')
        before = time.monotonic()
        raw_current = stream.get_time()
        after = time.monotonic()
        mapper = StreamClockMapper()  # Reviewed authored class embedded before release.
        mapper.bind(raw_current, before, after)
        self.mapper = mapper
        self.calibration = dict(bracket_seconds=after-before,
                                uncertainty_seconds=mapper.uncertainty_seconds,
                                source='PortAudio stream time; not acoustic calibration')
        return dict(self.calibration)

    def sample_time(self, info, now):
        if type(now) not in (int, float) or not math.isfinite(now):
            raise ValueError('speaker_aec_timestamp_invalid')
        callback_time(info, self.field, now)  # Existing finite and relative-offset bounds.
        if self.mapper is None:
            raise ValueError('speaker_aec_timestamp_invalid')
        current, sample = info['current_time'], info[self.field]
        if self.last_current is not None and current < self.last_current:
            raise ValueError('speaker_aec_timestamp_invalid')
        if self.last_sample is not None and sample <= self.last_sample:
            if self.field == 'output_buffer_dac_time':
                exc = ValueError('speaker_aec_playback_clock_reversed')
                exc.reference_delta_seconds = sample - self.last_sample
                raise exc
            raise ValueError('speaker_aec_capture_discontinuity')
        try:
            stamp = self.mapper.map(current, sample)
        except ValueError:
            raise ValueError('speaker_aec_timestamp_invalid') from None
        if not math.isfinite(stamp):
            raise ValueError('speaker_aec_timestamp_invalid')
        self.last_current, self.last_sample = current, sample
        return stamp


class Reference:
    """Bounded final-playout history, sampled at the capture frame's ADC time."""
    def __init__(self):
        self.chunks = deque(maxlen=64)  # at most 1.28s/40KiB mono PCM
        self.lock = threading.Lock()
        self.resampler = None
        self.last_time = None
        self.silent_since = None
        self.idle_underflows = 0
        self.idle_underflow_times = deque()
        self.idle_silence_bridges = 0

    def offer(self, pcm, start, status=0, *, whole_block_silent=None):
        import audioop
        if len(pcm) != 960 * 4:
            raise ValueError('speaker_aec_requires_20ms_48k_stereo')
        with self.lock:
            if self.last_time is not None and start <= self.last_time:
                exc=ValueError('speaker_aec_playback_clock_reversed');exc.reference_delta_seconds=start-self.last_time;raise exc
            # Inspect final stereo bytes, not a downmix that can cancel opposite
            # phase music. Never continue through active/recent output faults.
            silent = not any(pcm)
            continuous = self.last_time is not None and start - self.last_time <= .1
            gap=None if self.last_time is None else start-self.last_time
            # Preserve an already-established all-zero run over a bounded
            # unflagged scheduling gap. A silent prefix of an active host block
            # cannot authorize this. No samples or timestamps are invented.
            bridge=(not status and silent and whole_block_silent is not False
                    and gap is not None and .1 < gap <= MAX_IDLE_SILENCE_GAP_SECONDS
                    and self.silent_since is not None and self.last_time-self.silent_since >= IDLE_SECONDS)
            recovered = False
            if status:
                idle_gap = self.last_time is not None and 0 < start-self.last_time <= MAX_IDLE_SILENCE_GAP_SECONDS
                idle = (silent and idle_gap and self.silent_since is not None
                        and start - self.silent_since >= IDLE_SECONDS)
                if status != OUTPUT_UNDERFLOW or not idle:
                    exc=ValueError('speaker_aec_playback_discontinuity')
                    exc.playback_diagnostics=dict(current_output_silent=silent,reference_gap_seconds=None if self.last_time is None else round(start-self.last_time,6),known_silence_seconds=None if self.silent_since is None else round(start-self.silent_since,6))
                    raise exc
                # Isolated silent scheduling misses cannot consume a lifetime
                # allowance. Still stop a burst, even across second boundaries.
                # Existing silence/history/clock checks above remain mandatory.
                while (self.idle_underflow_times and
                       start - self.idle_underflow_times[0] >= IDLE_UNDERFLOW_WINDOW_SECONDS):
                    self.idle_underflow_times.popleft()
                if len(self.idle_underflow_times) >= MAX_IDLE_UNDERFLOWS:
                    raise ValueError('speaker_aec_idle_underflow_limit')
                recovered = True
            if bridge:
                while self.idle_underflow_times and start-self.idle_underflow_times[0]>=IDLE_UNDERFLOW_WINDOW_SECONDS:self.idle_underflow_times.popleft()
                if len(self.idle_underflow_times)>=MAX_IDLE_UNDERFLOWS:raise ValueError('speaker_aec_idle_underflow_limit')
                self.idle_underflow_times.append(start);self.idle_silence_bridges+=1
            mono = audioop.tomono(pcm, 2, .5, .5)
            mono, self.resampler = audioop.ratecv(mono, 2, 1, 48000, RATE, self.resampler)
            values = array.array('h', mono)
            if len(values) != SAMPLES:
                raise ValueError('speaker_aec_resampler_frame_invalid')
            if not silent:
                self.silent_since = None
            elif self.silent_since is None or not (continuous or bridge or recovered):
                self.silent_since = start
            if recovered:
                self.idle_underflows += 1
                self.idle_underflow_times.append(start)
            self.last_time = start
            self.chunks.append((start, values))
            return recovered

    def frame(self, start):
        with self.lock:
            chunks = tuple(self.chunks)
        result = array.array('h', [0]) * SAMPLES
        covered = 0
        # Select a reference sample for each capture time. Never reuse an old
        # nonzero frame after cancellation, a gap, or idle output.
        for stamp, values in chunks:
            begin = max(0, math.ceil((stamp - start) * RATE - .01))
            end = min(SAMPLES, math.ceil((stamp - start) * RATE + len(values) - .01))
            if begin>=end:continue
            phase=(start-stamp)*RATE
            # At equal16k rates, consecutive samples map to a contiguous slice.
            # Preserve the original per-sample rounding at half-sample ties,
            # including floating-point timestamp uncertainty at long uptimes.
            if abs(abs(phase-round(phase))-.5)>1e-4:
                first=round(phase)+begin
                if first<0:begin-=first;first=0
                end=min(end,begin+len(values)-first)
                if begin<end:
                    result[begin:end]=values[first:first+end-begin]
                    covered+=end-begin
                continue
            for i in range(begin, end):
                j = round((start + i / RATE - stamp) * RATE)
                if 0 <= j < len(values):
                    result[i] = values[j]
                    covered += 1
        return result.tobytes(), min(SAMPLES, covered)

    def clear(self):
        with self.lock:
            self.chunks.clear()


class EchoControl:
    def __init__(self, engine=None):
        self.engine = engine if engine is not None else CausalEcho()
        self.reference = Reference()
        self.lock = threading.Lock()
        self.closed = False
        self.faulted = False
        self.frames = self.reference_samples = 0
        self.max_processing_seconds = 0
        self.capture_worker = None
        self.output_callback_periods = 1
        self.last_playback_arrival = None
        self.idle_timestamp_overlap_times = deque()
        self.idle_timestamp_overlaps = 0
        self.last_idle_timestamp_overlap = None
        # Distinct streams are calibrated before start through their own clock;
        # neither is first-anchored or reanchored by callback scheduling.
        self.playback_clock = StreamClock('output_buffer_dac_time')
        self.capture_clock = StreamClock('input_buffer_adc_time')

    def playback(self, pcm, info, now=None, status=0):
        if self.closed:
            return
        periods = self.output_callback_periods
        if type(periods) is not int or periods not in (1, 3) or len(pcm) != 3840*periods:
            raise ValueError('speaker_aec_requires_20ms_48k_stereo')
        arrived = time.monotonic() if now is None else now
        if type(arrived) not in (int, float) or not math.isfinite(arrived):
            raise ValueError('speaker_aec_timestamp_invalid')
        start = self.playback_clock.sample_time(info, arrived)
        # A larger host block is still the exact final PCM, split at20ms sample
        # boundaries for capture-time alignment. Never hide an active fault in
        # a silent prefix of a mixed block. One host status is counted once.
        if status and any(pcm):
            raise ValueError('speaker_aec_playback_discontinuity')
        recovered = False
        block_silent=not any(pcm)
        # A host DAC-clock overlap during established digital silence may
        # repeat only known-zero reference time. Omit the whole actual block;
        # never clamp/reanchor its stamps, alter output, or clear reference.
        # Reference.offer remains strict for every block actually admitted.
        previous = self.reference.last_time
        if previous is not None and start <= previous:
            gap = None if self.last_playback_arrival is None else arrived-self.last_playback_arrival
            overlap = previous-start
            idle = (self.reference.silent_since is not None
                    and previous-self.reference.silent_since >= IDLE_SECONDS)
            allowed = (type(status) is int and status == 0 and block_silent
                       and idle and 0 <= overlap <= periods*.02
                       and gap is not None and 0 < gap <= MAX_IDLE_SILENCE_GAP_SECONDS)
            if allowed:
                while self.idle_timestamp_overlap_times and arrived-self.idle_timestamp_overlap_times[0] >= 1.0:
                    self.idle_timestamp_overlap_times.popleft()
                allowed = len(self.idle_timestamp_overlap_times) < 2
            if not allowed:
                exc=ValueError('speaker_aec_playback_clock_reversed')
                exc.reference_delta_seconds=start-previous
                exc.playback_diagnostics=dict(current_output_silent=block_silent,
                    reference_gap_seconds=start-previous,
                    known_silence_seconds=None if self.reference.silent_since is None else previous-self.reference.silent_since)
                raise exc
            self.idle_timestamp_overlap_times.append(arrived)
            self.idle_timestamp_overlaps += 1
            self.last_idle_timestamp_overlap=dict(reference_delta_seconds=start-previous,
                callback_arrival_gap_seconds=gap,host_block_seconds=periods*.02,
                known_digital_silence_seconds=previous-self.reference.silent_since,
                window_count=len(self.idle_timestamp_overlap_times))
            self.last_playback_arrival=arrived
            return False  # Not an underflow recovery; separately reported.
        for index in range(periods):
            recovered = self.reference.offer(pcm[index*3840:(index+1)*3840],
                                            start + index*.02,
                                            status if index == 0 else 0,whole_block_silent=block_silent) or recovered
        self.last_playback_arrival=arrived
        return recovered

    def capture(self, pcm, info, now=None):
        if len(pcm) != FRAME_BYTES:
            raise ValueError('speaker_aec_requires_20ms_16k_mono')
        stamp = self.capture_clock.sample_time(info, time.monotonic() if now is None else now)
        with self.lock:
            if self.closed:
                raise ValueError('speaker_aec_closed')
            started = time.monotonic()
            reference, covered = self.reference.frame(stamp)
            cleaned = self.engine.process(pcm, reference)
            if len(cleaned) != len(pcm):
                raise ValueError('speaker_aec_output_frame_invalid')
            self.frames += 1
            self.reference_samples += covered
            self.max_processing_seconds = max(self.max_processing_seconds, time.monotonic() - started)
            return cleaned

    def close(self):
        with self.lock:
            if not self.closed:
                self.closed = True
                self.engine.close()
                self.reference.clear()

    async def aclose(self):
        import asyncio
        if self.capture_worker is not None:
            if not await asyncio.to_thread(self.capture_worker.close,2):
                raise ValueError('speaker_aec_capture_worker_shutdown_timeout')
        else:await asyncio.to_thread(self.close)


def attach(result, settings, echo, events, *, threaded_capture=False):
    """Install after music mixing, before Pipecat opens either audio stream."""
    if settings.output_rate != 48000 or settings.output_channels != 2:
        raise ValueError('speaker_aec_requires_existing_48k_stereo_output')
    _, _, transport, processor = result
    capture = transport.input()._audio_in_callback
    playback = transport.output().callback
    close = transport.close
    transport.state.input_clock_calibrator = echo.capture_clock.calibrate
    transport.state.output_clock_calibrator = echo.playback_clock.calibrate
    def fault(exc, stage, work_seconds=None):
        if not echo.faulted:
            echo.faulted = True
            code = str(exc) if type(exc) is ValueError and str(exc) in FAULT_CODES else 'speaker_aec_processing_failed'
            detail={}
            delta=getattr(exc,'reference_delta_seconds',None)
            if code=='speaker_aec_playback_clock_reversed' and type(delta) in (int,float) and math.isfinite(delta):detail['reference_delta_seconds']=round(delta,6)
            if work_seconds is not None:detail['callback_work_seconds']=round(work_seconds,6)
            diagnostic=getattr(exc,'playback_diagnostics',None)
            if isinstance(diagnostic,dict):
                for key in ('current_output_silent','reference_gap_seconds','known_silence_seconds'):
                    value=diagnostic.get(key)
                    if type(value) is bool or type(value) in (int,float) and math.isfinite(value):detail[key]=value
            transport.state.post('speaker_aec_fault', error_type=type(exc).__name__,
                                 stage=stage, code=code, session_stopping=True,**detail)
            loop = transport.state.loop
            if loop and not loop.is_closed():
                loop.call_soon_threadsafe(processor.pipeline_failed.set)
            else:
                processor.pipeline_failed.set()
    def filtered(data, count, info, status):
        try:
            if status or echo.faulted:
                raise ValueError('speaker_aec_capture_discontinuity')
            data = echo.capture(data, info)
        except Exception as exc:
            fault(exc, 'capture')
            # Fault only: stop the whole session instead of silently falling back
            # to echo-contaminated ASR. No routine playback-time mic muting.
            data = bytes(len(data))
        return capture(data, count, info, status)
    if threaded_capture:
        from me3_assistant.capture_worker import CaptureWorker
        inp=transport.input()
        if not all(callable(getattr(inp,n,None)) for n in ['record_capture','deliver_capture','capture_ack']):
            raise ValueError('project_capture_delivery_hooks_required')
        def process(data,info,captured_at):
            return echo.capture(data,info,now=captured_at)
        capture_periods=getattr(transport.state,'input_callback_periods',1)
        if type(capture_periods) is not int or capture_periods not in (1,4):raise ValueError('invalid_input_callback_profile')
        worker=CaptureWorker(process,inp.deliver_capture,fault,echo.close,capacity=12 if capture_periods==4 else 5,batch_frames=capture_periods)
        echo.capture_worker=worker
        def filtered(data,count,info,status):
            captured_at=time.monotonic();inp.record_capture(status,time.monotonic_ns())
            if status or echo.faulted:
                fault(ValueError('speaker_aec_capture_discontinuity'),'capture');worker.stop.set()
            else:
                try:
                    if not isinstance(data,bytes) or len(data)!=FRAME_BYTES*capture_periods or count!=SAMPLES*capture_periods:raise ValueError('speaker_aec_requires_20ms_16k_mono')
                    frames=[]
                    for index in range(capture_periods):
                        frame_info=dict(info)
                        if index:frame_info['input_buffer_adc_time']=info['input_buffer_adc_time']+index*.02
                        # Keep one real callback arrival time and exact ADC
                        # offsets. Never fabricate callbacks, padding or samples.
                        frames.append((data[index*FRAME_BYTES:(index+1)*FRAME_BYTES],SAMPLES,frame_info,status,captured_at))
                    worker.submit_batch(tuple(frames))
                except Exception as exc:fault(exc,'capture');worker.stop.set()
            return inp.capture_ack()
    def referenced(data, count, info, status):
        # Map PortAudio time_info at callback entry, before generation/mixing.
        # Callback work must not turn into false DAC drift.
        arrived=time.monotonic()
        pcm, flag = playback(data, count, info, status)
        try:
            if echo.faulted:
                return pcm, flag
            overlap_count=echo.idle_timestamp_overlaps
            if echo.playback(pcm, info, now=arrived, status=status):
                transport.state.post('speaker_aec_idle_output_underflow', status=OUTPUT_UNDERFLOW,
                                     count=echo.reference.idle_underflows,
                                     window_count=len(echo.reference.idle_underflow_times),
                                     window_seconds=IDLE_UNDERFLOW_WINDOW_SECONDS,
                                     known_digital_silence_seconds=IDLE_SECONDS)
            if echo.idle_timestamp_overlaps != overlap_count:
                transport.state.post('speaker_aec_idle_timestamp_overlap',
                    count=echo.idle_timestamp_overlaps,reference_block_omitted=True,
                    output_unchanged=True,**echo.last_idle_timestamp_overlap)
        except Exception as exc:
            fault(exc, 'playback',work_seconds=max(0,time.monotonic()-arrived))
        return pcm, flag  # never change already-fenced/mixed output
    async def closed():
        try:
            try:await echo.aclose()
            finally:await close()
        finally:
            if echo.closed:events.emit('speaker_aec_closed', capture_frames=echo.frames,
                        reference_samples=echo.reference_samples,
                        max_processing_seconds=echo.max_processing_seconds,
                        idle_output_underflows=echo.reference.idle_underflows,
                        established_silence_gap_bridges=echo.reference.idle_silence_bridges,
                        audio_retained=False)
            if echo.capture_worker:
                w=echo.capture_worker
                events.emit('speaker_aec_capture_worker_closed',closed=w.closed.is_set(),delivered_frames=w.frames,
                            peak_queued_frames=w.peak,max_queue_wait_seconds=w.max_wait_seconds,
                            discarded_on_shutdown=w.discarded_on_close,audio_retained=False)
    transport.input()._audio_in_callback = filtered
    transport.output().callback = referenced
    transport.close = closed
    metadata = getattr(echo.engine, 'metadata', {
        'engine':'speex_causal_residual', 'tail_ms':200,
        'residual_stage':True, 'causal_reference_margin_ms':20,
        'algorithmic_input_delay_ms':40})
    events.emit('speaker_aec_attached', rate=RATE, frame_ms=20, **metadata,
                reference='final output callback PCM; ADC/DAC software timestamp alignment',
                qualified=False, audio_retained=False,threaded_capture=threaded_capture,
                queued_frame_limit=(12 if capture_periods==4 else 5) if threaded_capture else 0,host_capture_callback_ms=20*capture_periods if threaded_capture else 20,
                dispatch_age_limit_seconds=.32 if threaded_capture and capture_periods==4 else None,whole_callback_admission=threaded_capture)
