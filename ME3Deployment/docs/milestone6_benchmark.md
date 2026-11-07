# M6 standardized engineering benchmark v1.0

Scope: user-directed reproducible Pi baseline, not an invented course latency target or calibrated acoustic benchmark. Accepted M1–M5 source/receipts are immutable. New modules wrap existing methods only in the benchmark process. No voice/model/VAD/PCM/buffer/provider/prompt optimization. Provider: accepted resident Piper/en_US-ljspeech-high, same Google ASR/text backends.

Launch (Pi Bash):

```sh
cd ~/me3-duplex
.venv/bin/python scripts/benchmark_m6.py console --run run-20261020-001 --new
```

Console accepts fixed JSON actions on stdin, no shell/ports/accounts: `{"action":"start","session":"A"}`, then after greeting `{"action":"cue","session":"A","trial":"A00"}`; speak protocol script; wait for actual output; `{"action":"report","session":"A","trial":"A00","outcome":"pass","audible":true,"response_correct":true}`. Status, stop, bounded authorized task preparation/independent verification, summary and finish actions are documented by console. One process/session, max600s/eight detected turns, no hidden restarts. 10s between cue starts protects request bursts; it is outside response timing. Pending speech attempt60s timeout stops batch. Provider/auth/quota/pipeline errors stop safely, with remaining scheduled trials unstarted/incomplete.

Fixed complete scripts and methods: benchmark_m6/protocol.py, frozen before live in evidence/m6/source-001.json and run manifest. A–D each retained Hello warmup plus five steady cases: arithmetic, long study request, capital, contextual landmark, numbers (20plannednormal total). E retained warmup/two audible count barge-ins and split/replacement. F retained warmup/split continuation and real five-pencil confirmed save/readback.31planned episodes/37expected ASR turns, six sessions; hard aggregate48ASR/45reasoning/54localTTS/onecommit,20TaskRPCcap/session. Existing Google allowance required; zero hostedTTS. No recording or transcript retention. Actual user instructions/prompts stored as fixed protocol, not ASR transcripts. No provider request for parsing or synthetic tests.

Boundaries: time.monotonic_ns on the Pi main process/audio thread, observed_ns separate from delayed log timestamp. User onset = Silero SPEAKING return (.15start), end = QUIET endpoint (.7stop), both detector/processing proxies. Last physical phoneme/onset unknown. MCP request-start to validated ASR/TTS result includes local IPC/serialization; GoogleSpeech.answer entry/return gives reasoning boundary. First nonzero PCM callback return-ready is software submission, not DAC/acoustic onset. Observer scans PCM for nonzero only, never changes/retains it. Cancellation observer precedes original interrupt; flush and first callback of newepoch separate. Estimated old-output drain = lastold nonzero callback +reported PortAudio output latency +20msblock; unknown device/room/acoustic uncertainty, not a measured physical stop. Human overlap/clear/no-replay reports are categorical, not timestamps. Same bootID verifies independent SQL verifier clock; no cross-host time subtraction. Observation overhead/input queue/scheduling delay retained as limitations; no calibration claim.

Metrics: onset-proxy/endpoint-to-first-nonzero-submission, ASR/reasoning/TTS/policy stages, cancellation/flush/new-epoch software stop and reported-latency drain estimate. Physical audible onset/lastoutput remain null unless defensible acoustic method added prospectively. Every scheduled/started/incidental/failed/timedout/canceled/unreported/unstarted trial counted; per-kind/global rates. Available latencies include semantic failures and have n; successful-only table separate. Missing/canceled stage values remain null, never zero. Median and nearest-rank p95 only n>=20, a project reporting policy with coarse tail and clustered fixed-script limitations. Cold greeting TTS, first-user warmup, steady normal and postcancel model restart separate; no cache-flush or synthetic speech in live trials.

Success: normal one actual detected utterance, coherent expected answer and audible completed output. Counting requires at least2actual utterances, physically overlapping user speech reported by operator, old count stopped/no replay, correct replacement, captured callback epochs/capture continued/no canceled generation restart. Split trials require2real detected segments, correct completion/replacement/no replay; loss of prefix/context is a retained failure. No forced VAD or replayed input.

M4: user explicitly approved deleting only prior five-pencil operatione9b5b19230d4448095d199e5c32d5d18 immediately before F02, then real spoken proposal/direct confirmation/re-add. Preserve notebooks/pens/full prior evidence; standalone SQL verifies exactly5pencils, operation/digest and persistence. Task timings onset-proxy->commit/externalverification include human confirmation and operator-control delays, separate from Task MCP durations. Never treat a tool success string/native0 as semantic success. If save fails, report actual remaining state without direct-write fallback.

Reproduce derived summary without providers:

```sh
.venv/bin/python scripts/benchmark_m6.py analyze --run run-20261020-001 --output reproduced-001.json
```

Windows uses identical protocol/reducer/observer tests; it does not stand in for Pi live hardware. Run full regressions after benchmark. Retain M5 semantic and turn-budget failures as prior-context evidence, not secretly remove them or merge them into a new denominator. Standardized course rubric/acoustic reference setup remain unavailable; no course PASS inferred.
