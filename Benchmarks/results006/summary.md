# Cohort006 behavior-only post-scoring analysis

Judge-dependent semantics: **NOT_SCORED — readiness timeout**. Physical audible latency and semantic detection precision/recall: **NOT_MEASURED**.

| Upstream stage metric | n | mean (s) | median (s) | p95 (s) | upstream median CI95 |
|---|---:|---:|---:|---:|---|
| e2e | 0 | None | None | None | [None, None] |
| e2e_heard | 0 | None | None | None | [None, None] |
| endpointing | 0 | None | None | None | [None, None] |
| asr_final | 0 | None | None | None | [None, None] |
| llm_ttft | 0 | None | None | None | [None, None] |
| tts_ttfb | 0 | None | None | None | [None, None] |

Inventory: {"cancel_mark_trials": 0, "course_no_reply_count": 5, "data_state_counts": {"SCORED_TIMING": 9, "UNSTARTED": 5}, "expected_executions": 14, "expected_stage_executions": 0, "interruption_mark_trials": 9, "queue_status_counts": {"done": 7, "pending": 3}}

Raw distributions and missingness are retained separately. No whole-benchmark or project pass is inferred.

- No end-to-end value derived from cancellation. Upstream audio/producer onsets do not independently establish substantive or complete replies; filler is not newly labelled as substantive.
- No owner transcripts, generated assistant replies, secrets, hostnames or filesystem paths exported. Prompts originate only from the pinned public dataset manifest.
- Failed/unstarted/missing trials are not successes and are not imputed into latency distributions.
- Voice metrics use upstream available-feature fallback, not a judge-confirmed Respond subset. UTMOS disabled.
- Cohort006 has zero stage trials;004 stage metrics remain separate, without pooling. No historical31 trials or aggregate metrics transferred. Supplemental intervals do not replace upstream outputs.
