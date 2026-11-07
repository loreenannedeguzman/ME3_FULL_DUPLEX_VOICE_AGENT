"""ME3 milestones 1-2: local device audio -> Pipecat -> configurable Gemini Live."""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
from pathlib import Path
import platform
import struct
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent


@dataclass
class Settings:
    api_key: str = field(repr=False)
    model: str = ""
    voice: str = "Aoede"
    input_device: int | None = None
    output_device: int | None = None
    max_seconds: int = 180
    output_rate: int = 24000
    output_channels: int = 1
    interruptions_enabled: bool = True

    @classmethod
    def load(cls, env=None, *, live=False):
        env = os.environ if env is None else env
        def device(name):
            value = env.get(name, "").strip()
            if not value:
                return None
            index = int(value)
            if index < 0:
                raise ValueError(f"{name} must be a nonnegative device index")
            return index
        seconds = int(env.get("MAX_SESSION_SECONDS", "180"))
        if not 10 <= seconds <= 600:
            raise ValueError("MAX_SESSION_SECONDS must be between 10 and 600")
        result = cls(
            api_key=env.get("GOOGLE_API_KEY", "").strip(),
            model=env.get("GEMINI_MODEL", "").strip(),
            voice=env.get("GEMINI_VOICE", "Aoede").strip() or "Aoede",
            input_device=device("INPUT_DEVICE_INDEX"),
            output_device=device("OUTPUT_DEVICE_INDEX"),
            max_seconds=seconds,
        )
        if live:
            missing = []
            if not result.api_key or result.api_key.lower() in {"replace-me", "your-key-here"}:
                missing.append("GOOGLE_API_KEY")
            if not result.model:
                missing.append("GEMINI_MODEL (choose a model available to your account)")
            if env.get("ALLOW_PROVIDER_AUDIO", "") != "1":
                missing.append("ALLOW_PROVIDER_AUDIO=1 (authorizes sending microphone audio to Google; charges may apply)")
            if missing:
                raise ValueError("Live session blocked: configure " + "; ".join(missing))
        return result


class EventLog:
    """Metadata only: no transcripts, raw audio, API keys or provider payloads."""
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=False)
        self.path = directory / "events.jsonl"
        self.file = self.path.open("x", encoding="utf-8")
        self.counts = {}
        self.input_frames = 0
        self.output_frames = 0
        self.error_seen = False

    def emit(self, event, **metadata):
        self.counts[event] = self.counts.get(event, 0) + 1
        if metadata.get("frame_type") in {"ErrorFrame", "FatalErrorFrame"}:
            self.error_seen = True
        row = {
            "event": event,
            "monotonic_ns": time.monotonic_ns(),
            "wall_time": datetime.now(timezone.utc).isoformat(),
            **metadata,
        }
        self.file.write(json.dumps(row) + "\n")
        self.file.flush()

    def close(self):
        self.file.close()


def list_devices():
    import pyaudio
    audio = pyaudio.PyAudio()
    try:
        devices = []
        for index in range(audio.get_device_count()):
            d = audio.get_device_info_by_index(index)
            devices.append({
                "index": index, "name": d["name"],
                "input_channels": int(d["maxInputChannels"]),
                "output_channels": int(d["maxOutputChannels"]),
                "default_rate": d["defaultSampleRate"],
            })
        return devices
    finally:
        audio.terminate()


def configure_pi_audio(settings):
    """Use project-local ALSA conversion routes; never edit ~/.asoundrc."""
    config = ROOT / "config" / "asound-pi.conf"
    if platform.system() != "Linux" or not config.exists():
        return
    if "ALSA_CONFIG_PATH" in os.environ and os.environ["ALSA_CONFIG_PATH"] != str(config):
        return  # Explicit operator configuration takes precedence.
    os.environ["ALSA_CONFIG_PATH"] = str(config)
    settings.output_rate = 48000
    settings.output_channels = 2
    devices = list_devices()
    for attribute, route, channel in (
        ("input_device", "me3_mic", "input_channels"),
        ("output_device", "me3_speaker", "output_channels"),
    ):
        if getattr(settings, attribute) is None:
            matches = [d["index"] for d in devices if d["name"] == route and d[channel] > 0]
            if not matches:
                raise ValueError(f"Project ALSA route {route} is unavailable; inspect config/asound-pi.conf")
            setattr(settings, attribute, matches[0])



def audio_probe(settings: Settings):
    """Short non-network diagnostic. Does not retain mic audio or prove audibility."""
    import pyaudio
    audio = pyaudio.PyAudio()
    captured = played = 0
    peak = 0
    inp = out = None
    try:
        inp = audio.open(
            format=pyaudio.paInt16, channels=1, rate=16000, input=True,
            input_device_index=settings.input_device, frames_per_buffer=320,
        )
        out = audio.open(
            format=pyaudio.paInt16, channels=settings.output_channels, rate=settings.output_rate, output=True,
            output_device_index=settings.output_device,
        )
        # Two seconds of microphone reads plus a quiet 440Hz diagnostic tone.
        # This diagnostic is not a prerecorded conversation or a voice test.
        for i in range(100):
            samples = inp.read(320, exception_on_overflow=True)
            captured += len(samples)
            values = struct.unpack("<" + "h" * (len(samples) // 2), samples)
            peak = max(peak, max(map(abs, values), default=0))
            size = settings.output_rate // 50
            tone = b"".join(struct.pack("<h", int(1200 * math.sin(
                2 * math.pi * 440 * (i * size + j) / settings.output_rate
            ))) for j in range(size))
            if settings.output_channels == 2:
                import audioop
                tone = audioop.tostereo(tone, 2, 1, 1)
            out.write(tone)
            played += len(tone)
        return {
            "status": "software_device_io_completed",
            "microphone_bytes_read": captured, "microphone_peak": peak,
            "tone_bytes_written": played, "audible_output": "NOT_VERIFIED",
            "live_conversation": "NOT_TESTED",
        }
    finally:
        for stream in (inp, out):
            if stream is not None:
                stream.stop_stream()
                stream.close()
        audio.terminate()



def output_channel_adapter(channels):
    """Preserve duration while duplicating mono speech for a stereo hardware sink."""
    from pipecat.frames.frames import OutputAudioRawFrame
    from pipecat.processors.frame_processor import FrameProcessor

    class ChannelAdapter(FrameProcessor):
        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if isinstance(frame, OutputAudioRawFrame) and channels == 2 and frame.num_channels == 1:
                import audioop
                frame.audio = audioop.tostereo(frame.audio, 2, 1, 1)
                frame.num_channels = 2
                frame.num_frames = len(frame.audio) // 4
            await self.push_frame(frame, direction)
    return ChannelAdapter()


def build_pipeline(settings: Settings, events: EventLog):
    """Construct actual Pipecat objects. No provider connection until runner.run."""
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.worker import PipelineParams, PipelineWorker, ProcessorUnusablePolicy
    from pipecat.processors.aggregators.llm_context import LLMContext
    from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
    from pipecat.processors.frame_processor import FrameProcessor
    from pipecat.services.google.gemini_live.llm import GeminiLiveLLMService
    from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams
    from pipecat.workers.runner import WorkerRunner

    class MetadataTap(FrameProcessor):
        def __init__(self, stage):
            super().__init__()
            self.stage = stage
            self.audio_frames = 0

        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            name = type(frame).__name__
            if name in {"InputAudioRawFrame", "OutputAudioRawFrame", "TTSAudioRawFrame"}:
                self.audio_frames += 1
                if self.stage == "microphone":
                    events.input_frames += 1
                elif name != "InputAudioRawFrame":
                    events.output_frames += 1
                if self.audio_frames == 1 or self.audio_frames % 50 == 0:
                    events.emit("audio_frame", stage=self.stage, frame_type=name,
                                cumulative_frames=self.audio_frames,
                                sample_rate=frame.sample_rate, byte_count=len(frame.audio))
            elif name in {
                "TranscriptionFrame", "LLMFullResponseStartFrame",
                "LLMFullResponseEndFrame", "BotStartedSpeakingFrame",
                "BotStoppedSpeakingFrame", "UserStartedSpeakingFrame",
                "UserStoppedSpeakingFrame", "ErrorFrame", "FatalErrorFrame",
                "InterruptionFrame", "CancelFrame", "EndFrame",
            }:
                events.emit("pipeline_frame", stage=self.stage, frame_type=name,
                            frame_id=frame.id, direction=direction.name)
            await self.push_frame(frame, direction)

    if settings.interruptions_enabled:
        from duplex_audio import make_duplex_transport
        transport, duplex_state = make_duplex_transport(settings, events)
    else:
        duplex_state = None
        transport = LocalAudioTransport(LocalAudioTransportParams(
            audio_in_enabled=True, audio_out_enabled=True,
            audio_out_channels=settings.output_channels,
            input_device_index=settings.input_device,
            output_device_index=settings.output_device,
        ))

    class ObservedGemini(GeminiLiveLLMService):
        # Pinned Pipecat 1.12 hook: log only the interruption flag, never payloads.
        async def _handle_server_message(self, message):
            if message.server_content and message.server_content.interrupted:
                events.emit("provider_interrupted",
                            capture_count=duplex_state.capture_count if duplex_state else None)
            await super()._handle_server_message(message)

    service = ObservedGemini(
        api_key=settings.api_key,
        settings=GeminiLiveLLMService.Settings(
            model=settings.model, voice=settings.voice,
            system_instruction=(
                "You are a concise, friendly voice assistant for a student project. "
                "Normally reply briefly and remember earlier turns. "
                "When asked to count slowly to thirty, say all thirty numbers in words with pauses. "
                "When interrupted, abandon the old answer and address the newest request only. "
                "You have no tools and must not claim to perform external actions."
            ),
        ),
        inference_on_context_initialization=True,
    )
    context = LLMContext([{"role": "user", "content": "Greet me briefly and say: I am ready. Please say hello."}])
    if settings.interruptions_enabled:
        from pipecat.audio.vad.silero import SileroVADAnalyzer
        from pipecat.audio.vad.vad_analyzer import VADParams
        from pipecat.processors.aggregators.llm_response_universal import LLMUserAggregatorParams
        from pipecat.turns.user_turn_strategies import UserTurnStrategies
        from pipecat.turns.user_start import VADUserTurnStartStrategy
        from pipecat.turns.user_stop import SpeechTimeoutUserTurnStopStrategy
        from duplex_audio import make_response_fence
        user_params = LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(params=VADParams(
                confidence=0.7, start_secs=0.15, stop_secs=0.2, min_volume=0.6
            )),
            user_turn_strategies=UserTurnStrategies(
                start=[VADUserTurnStartStrategy()],
                stop=[SpeechTimeoutUserTurnStopStrategy(
                    user_speech_timeout=0.2, wait_for_transcript=False
                )],
            ),
        )
        user, assistant = LLMContextAggregatorPair(context, user_params=user_params)
        response_processors = [MetadataTap("provider_output"), make_response_fence(events)]
        events.emit("duplex_configuration", vad="Silero ONNX CPU",
                    confidence=0.7, start_secs=0.15, stop_secs=0.2,
                    min_volume=0.6, turn_stop_secs=0.2,
                    provider_vad="enabled; local frames supplemental",
                    output_buffer_ms=120)
    else:
        user, assistant = LLMContextAggregatorPair(context)
        response_processors = [MetadataTap("provider_output")]
    pipeline = Pipeline([
        transport.input(), MetadataTap("microphone"), user, service,
        *response_processors, output_channel_adapter(settings.output_channels),
        transport.output(), assistant,
    ])
    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(audio_in_sample_rate=16000, audio_out_sample_rate=settings.output_rate),
        idle_timeout_secs=settings.max_seconds,
        processor_unusable_policy=ProcessorUnusablePolicy.END,
    )
    runner = WorkerRunner()
    return runner, worker, transport


async def run_session(settings, events):
    runner, worker, transport = build_pipeline(settings, events)
    events.emit("session_start", model=settings.model, host=platform.system(),
                machine=platform.machine(), max_seconds=settings.max_seconds)
    await runner.add_workers(worker)
    from pipecat.frames.frames import LLMRunFrame
    await worker.queue_frames([LLMRunFrame()])
    events.emit("initial_context_queued")
    try:
        await asyncio.wait_for(runner.run(), timeout=settings.max_seconds)
    except asyncio.TimeoutError:
        events.emit("session_limit_reached")
        await runner.cancel()
    finally:
        if hasattr(transport, "close"):
            await transport.close()
        events.emit("session_end", input_frames=events.input_frames,
                    provider_output_frames=events.output_frames, error_seen=events.error_seen)
    if events.error_seen or not events.output_frames:
        raise ValueError("Session ended with an error or without response audio; inspect the event log and provider/model configuration")


async def run_local_pipeline_probe(settings, events):
    """Run real Pipecat device input/output for 3s, using only a diagnostic tone."""
    from pipecat.frames.frames import InputAudioRawFrame, OutputAudioRawFrame
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.worker import PipelineParams, PipelineWorker
    from pipecat.processors.frame_processor import FrameProcessor
    from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams
    from pipecat.workers.runner import WorkerRunner

    counts = {"microphone_frames": 0, "tone_frames_before_output": 0}

    class ProbeTap(FrameProcessor):
        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if isinstance(frame, InputAudioRawFrame):
                counts["microphone_frames"] += 1
            elif isinstance(frame, OutputAudioRawFrame):
                counts["tone_frames_before_output"] += 1
                events.emit("diagnostic_tone_queued", byte_count=len(frame.audio))
            await self.push_frame(frame, direction)

    if settings.interruptions_enabled:
        from duplex_audio import make_duplex_transport
        transport, duplex_state = make_duplex_transport(settings, events)
    else:
        transport = LocalAudioTransport(LocalAudioTransportParams(
            audio_in_enabled=True, audio_out_enabled=True,
            audio_out_channels=settings.output_channels,
            input_device_index=settings.input_device,
            output_device_index=settings.output_device,
        ))
    worker = PipelineWorker(
        Pipeline([transport.input(), ProbeTap(), output_channel_adapter(settings.output_channels), transport.output()]),
        params=PipelineParams(audio_in_sample_rate=16000, audio_out_sample_rate=settings.output_rate),
        idle_timeout_secs=8,
    )
    runner = WorkerRunner()
    await runner.add_workers(worker)
    running = asyncio.create_task(runner.run())
    try:
        await asyncio.sleep(1)
        tone = b"".join(struct.pack("<h", int(1200 * math.sin(
            2 * math.pi * 440 * j / 24000
        ))) for j in range(12000))
        await worker.queue_frames([OutputAudioRawFrame(
            audio=tone, sample_rate=24000, num_channels=1
        )])
        if settings.interruptions_enabled:
            from pipecat.frames.frames import InterruptionFrame
            await asyncio.sleep(0.15)
            await worker.queue_frames([InterruptionFrame()])
            await asyncio.sleep(0.5)
            await worker.queue_frames([OutputAudioRawFrame(
                audio=tone, sample_rate=24000, num_channels=1
            )])
        await asyncio.sleep(2)
    finally:
        await runner.cancel()
        await asyncio.wait_for(running, timeout=5)
        if hasattr(transport, "close"):
            await transport.close()
    events.emit("local_pipeline_probe", **counts, provider_connected=False,
                audible_output="NOT_VERIFIED")
    if not counts["microphone_frames"] or not counts["tone_frames_before_output"]:
        raise ValueError("Local Pipecat probe did not observe both input and diagnostic output frames")
    print(json.dumps({**counts, "provider_connected": False,
                      "audible_output": "NOT_VERIFIED", "live_conversation": "NOT_TESTED"}))



def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--list-devices", action="store_true")
    mode.add_argument("--audio-test", action="store_true", help="2s local mic/tone diagnostic; no API")
    mode.add_argument("--preflight", action="store_true", help="safe configuration/device report; no API")
    mode.add_argument("--build-check", action="store_true", help="construct real pipeline without connecting")
    mode.add_argument("--pipeline-test", action="store_true", help="3s real local Pipecat device pipeline; no API")
    parser.add_argument("--m1", action="store_true", help="legacy M1 transport without local interruption")
    args = parser.parse_args(argv)
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env", override=False, encoding="utf-8-sig")
        from loguru import logger
        # Provider libraries may log transcripts/payloads. M1 stores metadata only.
        logger.remove()
        def safe_sink(message):
            text = str(message)
            key = os.getenv("GOOGLE_API_KEY", "")
            if key:
                text = text.replace(key, "[REDACTED]")
            sys.stderr.write(text)
        logger.add(safe_sink, level="INFO", format="{level}: {message}", diagnose=False,
                   filter=lambda record: (record["level"].no >= 30 and "api_key" not in record["message"].lower()) or record["message"] == "Connected to Gemini service")
        settings = Settings.load(live=not any((
            args.list_devices, args.audio_test, args.preflight,
            args.build_check, args.pipeline_test,
        )))
        settings.interruptions_enabled = not args.m1
        configure_pi_audio(settings)
        if args.list_devices:
            print(json.dumps(list_devices(), indent=2))
            return 0
        if args.audio_test:
            print(json.dumps(audio_probe(settings), indent=2))
            return 0
        if args.preflight:
            print(json.dumps({
                "host": platform.system(), "machine": platform.machine(),
                "python": platform.python_version(),
                "live_target_acceptance": "NOT_VERIFIED",
                "detected_pi_model": (Path("/proc/device-tree/model").read_text().rstrip("\x00\n")
                                      if Path("/proc/device-tree/model").exists() else None),
                "google_key_present": bool(settings.api_key),
                "gemini_model_configured": bool(settings.model),
                "provider_audio_authorized": os.getenv("ALLOW_PROVIDER_AUDIO") == "1",
                "input_device": settings.input_device, "output_device": settings.output_device,
                "devices": list_devices(),
            }, indent=2))
            return 0
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + str(time.monotonic_ns())
        events = EventLog(ROOT / "evidence" / ("m2" if settings.interruptions_enabled else "m1") / ("runtime-" + run_id))
        try:
            if args.pipeline_test:
                asyncio.run(run_local_pipeline_probe(settings, events))
            elif args.build_check:
                # Placeholder never leaves this process; no runner.start/connect.
                settings.api_key = settings.api_key or "offline-build-placeholder"
                settings.model = settings.model or "offline-build-placeholder"
                async def construct_only():
                    _, _, transport = build_pipeline(settings, events)
                    if hasattr(transport, "close"):
                        await transport.close()
                asyncio.run(construct_only())
                events.emit("offline_pipeline_built", provider_connected=False)
                print("PASS: real Pipecat pipeline constructed; no provider/audio session started.")
            else:
                print("Live microphone audio will be sent to Google. Speak after connection; Ctrl+C to stop.")
                asyncio.run(run_session(settings, events))
        finally:
            events.close()
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        # Do not expose traceback/provider response or credentials in persistent logs.
        message = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        secret = os.getenv("GOOGLE_API_KEY", "")
        if secret:
            message = message.replace(secret, "[REDACTED]")
        print("ERROR: " + message, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
