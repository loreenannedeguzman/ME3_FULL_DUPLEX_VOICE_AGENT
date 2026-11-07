# F4 preparation and F7 measurement protocol — draft256

Preparation only: no benchmark, installation, scoring call, runtime change or acceptance decision. Preserve **F1 → F2 → F3 → F4 → F5 → F6 → F7 → F8** and the board’s existing gates. F4 fixes definitions/qualifies the bridge; F6 freezes the candidate; F7 requires separate authorization and allowance. The JSON is the detailed companion, not a new queue. Independent provider-free preparation may run in parallel where the board permits; completion gates still apply.

Use only the agreed course repository at **`106ac8e543a5fd996e254a87e69fa101bfd27a42`**, including its METHOD, GUIDE, scorer and released judge prompts. Other research suites mentioned in discussion have conceptual overlap; their implementations or execution are not claimed.

| Measurement layer | What is measured | Boundary and required reporting |
|---|---|---|
| **Pinned course metrics** | Pause/turn-taking TOR, missed turn-taking, idle false turn-taking, interruption response/relevance, v1.5 behavior, `t_stop`/`t_resp`, available voice changes | Keep exact supplied trial windows, segmentation, turn rule and judge. Eight seconds is a specified response window, not every task’s deadline or an overall grade. |
| **Added engineering timing** | Detection → validated disposition → cancellation → final old digital sample; first new content after user end | Same aligned monotonic/sample-clock trace and generation identity. Never relabel host cancellation or producer completion as course `t_stop` or physical cessation. |
| **Added engineering behavior** | Genuine takeover precision/recall; background false interrupts; premature floor; acknowledgment/resume continuity | Predeclared opportunity denominators and frozen one-to-one event matching. Timing or a filler alone cannot prove correct response/resumption. |
| **Added functional checks** | Context, voice/text handoff, correction/cancel, completed tasks, timeout/interruption/restart recovery, concurrent authorized background work | Independent current artifacts/readbacks, truthful partial outcomes and no replay/cross-authorization. These are separate functional outcomes, not automatically scored by the course benchmark. |
| **Physical experiment, only if performed** | Acoustic cessation/onset at calibrated capture/reference | Otherwise `NOT_MEASURED`. Digital traces/callbacks are never substituted for physical measurements. Calibration is not added as a prerequisite for the agreed direct-feed rig. |

## Timestamp ledger

Record integer **monotonic nanoseconds**, clock/boot/session identity, trial, generation and capture/request IDs. Audio boundaries also retain sample index/rate and the frozen mapping to the shared recording clock. Do not subtract different machines’ monotonic counters without qualified offset/drift alignment and uncertainty.

| Timestamp | Exact observation |
|---|---|
| `t_user_overlap_onset` | First active digital input sample in the labeled overlap; retain annotation/detector identity and scheduled clip onset separately. |
| `t_VAD` | Local VAD-confirmed onset for that capture, with detector/frame settings. |
| `t_decision` | Host validation of the current capture’s disposition, not an unvalidated token/prediction. |
| `t_cancel_signal` | Actual generation invalidation/cancel request. Suspension or acknowledgment is not cancellation. |
| `t_last_digital_old_audio` | Final old-generation speech-bearing sample at the digital playout boundary, supported by trace/track; not TTS/producer completion. |
| `t_first_new_content` | First content-specific new reply sample. Exclude greetings, filler, generic acknowledgment/progress and resumed old content. |
| `t_user_end` | Annotated/pinned-detector end of the relevant completed turn or overlap; not ASR completion. |
| `t_first_resumed_content` | Correct continuation of the retained original answer after overlap; distinct from new-request content. |
| `t_physical_cessation` | Separately measured acoustic cessation, otherwise null/`NOT_MEASURED`. |

Missing timestamps carry an explicit reason: not applicable, not measured, censored, missing trace or invalid alignment. Keep raw signed timing differences for diagnosis; apply any pinned course clamping only to that course metric.

## Added behavior and functional outcomes

| Outcome | Numerator / denominator or criterion |
|---|---|
| Genuine takeover precision | `TP / (TP + FP)` for one-to-one matched takeover/adoption events; unsupported and duplicate predictions are FP. |
| Genuine takeover recall / missed uptake | `TP / (TP + FN)`; unmatched genuine completed directed requests are FN. Report zero-denominator as not applicable. |
| False interrupt during background/third-person speech | Opportunities containing unjustified cancellation/adoption / **all evaluable not-directed opportunities**, overall and by kind. Opportunities are predeclared windows, not frames or only emitted interruptions. This differs from course idle false turn-taking. |
| Premature floor | Incomplete-turn/pause opportunities with early host floor adoption / all evaluable such opportunities. Report emitted meaningful speech separately; later usefulness does not erase the early takeover. |
| Acknowledgment/resume accuracy | Correct cases / all evaluable listening-acknowledgment cases. Require intended task/generation preservation, no false command/confirmation, and content continuity without skipped/duplicated substantive content. Report resumption delay too. |
| Context/cancellation correctness | Per-category explicit outcomes: voice/text handoff, topic return, correction vs replacement, targeted cancel, stale speech/effect suppression and no topic-change confirmation. A pooled score must not conceal a safety failure. |
| Task completion/recovery | Independently completed / started evaluable goals, and successful / started recovery cases. Keep completed, partial, failed and unstarted steps; fresh file/test/service proofs; no consumed/uncertain effect replay. Restart is claimed only when actually tested. |
| Concurrent background work | Foreground/background outcomes, foreground latency distributions and actual resource use under a frozen authorized bounded workload. Test additive vs replacement and targeted cancel; do not invent uncommanded jobs or background promises. |

**Predeclared engineering-only matching limits —403.** Early tolerance is 32 ms, the late matching window is 8 s, and maximum eligible causal-alignment interval width is 32 ms. These are predeclared eligibility limits, not measured accuracy or blanket calibration. Require exact trial/capture/generation and the entire causal interval within the eligible opportunity window. Use robust maximum-cardinality one-to-one matching, then minimum absolute distance; stable IDs resolve exact point ties. Interval-ambiguous alternatives, missing anchors and intervals wider than 32 ms remain `NOT_MEASURED`. Preserve premature-floor flags, duplicate/unmatched FP/FN and all raw domains. Analysis remains disabled until the exact source/adapter/route and qualification are frozen. The two authored 400 events establish only their scoped intervals; raw wrapper exit 1 is retained. Course windows, TOR, judge labels and scorer remain unchanged; do not tune these rules on final outcomes.

## Statistics, missingness and freeze

- Report **k/n**, denominators and 95% Wilson intervals for the pinned course rates. Engineering precision/recall and opportunity rates stay separate; disclose repeated-session dependence and any predeclared session-cluster bootstrap.
- Report observed **n, median, mean, p95** and the pinned median bootstrap interval (1000 draws, scorer bootstrap seed 0 (separate from the trial-queue seed), for n≥3). At n<3 the median interval is not estimated; at n<20 flag p95 as near the slowest observation, not a stable tail guarantee.
- Keep pinned responder-only latency conditional. Course v1 interruption TOR excludes `overlap_not_played` and reports those cases separately; v1.5 `t_stop` excludes `spoke_through`, and `t_resp` uses played overlaps with non-null response timing. Preserve those exact course populations, alongside misses/no-answer/not-played/failure counts and total attempted counts. A timeout is a censored latency plus its applicable failure/miss; never replace it with zero, 8 seconds or the timeout threshold, or silently remove it.
- Retain planned, started, completed-observed, failed, canceled, timed-out, unstarted, censored, invalid/missing-trace, not-measured and not-scored counts. Missing ASR/judge makes affected semantic results **NOT_SCORED**, not PASS/zero; duration-only verdicts are provisional. Optional missing UTMOSv2 is **NOT_SCORED**.
- Preserve the METHOD/prompt ambiguity: METHOD calls straight-through continuation Resume, while the released v1.5 prompt requires a new post-overlap semantic segment or Unknown. Freeze/disclose the actual prompt/scorer; do not infer semantic success from timing alone.
- Freeze source/adapter/configuration, models/voices/prompts, VAD/audio/pacing/buffers, dependencies, datasets/licenses/checksums, labels, seed/rounds/reset, clock alignment, matching/statistical rules, privacy/grants and recording retention **before F7**. No mid-run tuning, hidden retries, failed-sample replacement, refunds or mixing changed candidates.

Benchmark allowance remains separate from standing development. First live failure stops its packet/cohort; retained failures and unstarted trials remain visible. This draft contains no measured results and grants no execution or publication authority.

Development context only:254 produced `ack_resume` in 5.637061 seconds, exceeding its provisional 2-second engineering budget. It remains inactive; full-duplex acceptance is open. That one native probe is not a benchmark result or speaker/directedness qualification.

Pinned turn duration spans the first to last clipped response segment, including intervening gaps; it is not summed voiced duration. Preserve that code rule rather than silently substituting another duration definition.
