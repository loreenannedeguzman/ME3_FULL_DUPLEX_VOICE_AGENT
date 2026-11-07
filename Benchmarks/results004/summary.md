# Cohort004 post-scoring analysis

Judge-dependent semantics: **NOT_SCORED — readiness timeout**. Physical audible latency and semantic detection precision/recall: **NOT_MEASURED**.

| Upstream stage metric | n | mean (s) | median (s) | p95 (s) | upstream median CI95 |
|---|---:|---:|---:|---:|---|
| e2e | 21 | 6.439295238095239 | 6.4977 | 7.3566 | [6.0265, 6.7266] |
| e2e_heard | 18 | 7.285777777777778 | 7.348 | 8.0136 | [7.0280000000000005, 7.54] |
| endpointing | 30 | 0.6668833333333333 | 0.6355 | 0.6911999999999999 | [0.61985, 0.65315] |
| asr_final | 30 | 0.7902866666666667 | 0.60175 | 1.3154599999999994 | [0.568245, 0.7278287499999996] |
| llm_ttft | 24 | 4.7732375000000005 | 4.7321 | 6.168754999999999 | [4.2637, 5.0702] |
| tts_ttfb | 21 | 0.5065238095238096 | 0.5459 | 0.6944 | [0.3854, 0.607] |

Inventory: {"cancel_mark_trials": 0, "course_no_reply_count": 12, "data_state_counts": {"INFRASTRUCTURE_ERROR": 1, "SCORED_TIMING": 30, "UNSTARTED": 13}, "expected_executions": 44, "expected_stage_executions": 30, "interruption_mark_trials": 30, "queue_status_counts": {"done": 30, "failed": 1, "pending": 9}}

Raw distributions and missingness are retained separately. No whole-benchmark or project pass is inferred.

- No end-to-end value derived from cancellation. Upstream audio/producer onsets do not independently establish substantive or complete replies; filler is not newly labelled as substantive.
- No owner transcripts, generated assistant replies, secrets, hostnames or filesystem paths exported. Prompts originate only from the pinned public dataset manifest.
- Failed/unstarted/missing trials are not successes and are not imputed into latency distributions.
- Voice metrics use upstream available-feature fallback, not a judge-confirmed Respond subset. UTMOS disabled.
- No historical31 trials or aggregate metrics transferred. Supplemental intervals do not replace upstream outputs.
