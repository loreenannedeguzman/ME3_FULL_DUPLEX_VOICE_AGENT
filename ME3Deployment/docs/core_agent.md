# Core Agent Completion — focused functional phase

The user changed priority on20October2026: finish reliable core interaction first, then freeze a candidate and conduct the comprehensive standardized benchmark separately. Preserve all M1–M7 receipts, source and failures. No comprehensive benchmark is run here.

| Assignment requirement | Existing functional evidence | Remaining core defect / current gate |
| --- | --- | --- |
| Pi agent harness with MCP | Pipecat and real stdio ASR/TTS/Task MCP in M3–M7 | Revalidate new opt-in path onPi |
| Student-built Pi ASR MCP | Actual mic -> ASR server -> existing Google model, M3–M7 | Missed/extra segments, recognition mistakes/timeouts; connection/DNS tails |
| Student-built Pi TTS MCP | Actual residentPiper/neural voice and earphone playback; M5 listening preference | Natural rhythm/general quality incomplete; preserve same model/prosody |
| Pi VAD | Silero actual live speech and simultaneous capture | Missed Hello/extra short segments; no arbitrary threshold tuning |
| Full-duplex interruption/recovery | Repeated audible barge-ins plus epochs/capture evidence | Split recovery incomplete in fullrun; latest focused2/2 only; settle empty turns safely |
| Real-world task | Spoken proposal/confirmation, Task MCP/SQLite state change/independent read-back | Fresh bounded new item with old rows preserved |
| Natural conversation/context | Mango recall, Paris landmark follow-ups, operator voice improvement | Network-dependent delay, robustness and recognition accuracy not broadly proven |
| Standardized classwork benchmark | Engineering M6/M7 software evidence exists | Comprehensive final-candidate/acoustic/class-method validation deferred |
| Student oral defense | Evidence-linked notes | Student mastery not yet assessed |

Targeted change: successful ASR requests reuse one SDK/HTTP connection, while cancellation/error/deadline retires it before nextrequest. Keep per-request trace scope and one-wire guard, existing M7 bounded cleanup/status barrier, same GoogleLOW/FLAC. The caller-owned SDK aiohttp session uses one connection, explicit60second keepalive and300second DNS cache, preserving TLS verification and proxy handling. These are in-process cache bounds, not system DNS changes or response delays. Pinned SDK defaults use15second keepalive/10second DNS cache, which can expire between guided turns. The Pi ASR MCP lifespan closes the cached client and owned session at shutdown. Original M1–M7 source files remain unchanged; new me3_core modules/launcher only. Per-request mode remains explicit in config/core_agent.json, no automatic fallback.

Derived controller bounds actual work at55seconds, emits a settled marker on every turn and asks once for a complete request when recognition is empty. Existing VAD thresholds, capture, playback queue/epochs, Piper voice, task policy and original entrypoints are unchanged. A canceled/ambiguous utterance cannot authorize a task. Local request cancellation does not prove remote computation or hardware-buffered audio stopped.

Focused scripts are frozen in me3_core/scenarios.py: three sessions, at most eight detected utterances and600seconds per session. Planned18ASR segments, hard22ASR/12text/26localTTS/oneconfirmedcommit cap. One declared100ms local-deadline trial uses actual microphone capture and real ASR MCP; it is controlled timeout evidence, not a natural provider outage. New three-eraser item requires a correct proposal/direct spoken confirmation and a standalone read-only SQL check; no existing list rows are deleted. All failures/extra segments/native exits remain recorded. No raw microphone audio or ASR transcripts are retained.

Local fixtures establish software behavior only. Required actual Pi gates: ordinary/context responses, two audible overlaps with stopped/no-return playback, continuation/replacement, timeout notice then recovery without restart, exact new item/independent SQL and correct saved-list read-back. Stop unexpected provider/auth/quota errors, preserve failures, no hidden relaunch. Lightweight monotonic VAD/MCP/HTTP/settled/output-first markers and5second resource samples are not an acoustic latency benchmark. Resource RSS sums can double-count shared pages and miss short peaks.

Launch candidate (Pi Bash):

```sh
cd ~/me3-duplex
sh scripts/run_core_voice.sh
```

Controlled validation console (fresh run ID only):

```sh
.venv/bin/python scripts/core_live.py console --run core-20261020-001 --new
```

Existing accepted launches remain run_m7_voice.sh and run_piper_reuse_voice.sh. Live results, source hashes, regressions and remaining gates will be recorded after target execution; no full-functionality claim from tests alone.
