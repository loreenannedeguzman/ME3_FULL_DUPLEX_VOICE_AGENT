# Cohort005 failure-only disposition

The separately frozen behaviour-only005 cohort stopped during normal ASR worker preparation, before its first trial. The Pi reported `actual_asr_worker_readiness_failed` inside `prepare_speech_worker`; the outer orchestrator timed out waiting for Rig readiness. This does not establish the underlying provider/network cause.

There are **zero completed executions and no eligible scoring data**. One initialized normal-session evidence directory contained three files (3,605 bytes); cleanup and export were verified. Observed counters stayed1049 ASR /2065 reasoning /2453 TTS /2 commit items. No synthetic trial table, missing transcript replacement or005 score has been created. Scoring is NOT_RUN_NO_ELIGIBLE_DATA; judge calls are zero. There is no automatic retry or further cohort planned.

See [the failure summary](failure-summary.json), [the inert005 provenance](../ADAPTER005_HISTORICAL_PROVENANCE.json) and [adapter005 source](../adapter005/manifest.DRAFT.json). Its one qualified software case and adapter freeze preceded collection; neither proves actual ASR availability or successful behaviour trials. The normal production source remained unchanged. [Collection004 stage exports](../results004/README.md) remain separate and unchanged, including004's first behaviour fault and missing populations. F7 remains PARTIAL; no whole-project or paper-equivalence acceptance is claimed.

Private startup logs, provider traces, active contracts/bindings, session IDs and audio are omitted. The hashes identify actual private evidence without exporting it.

Historical clarification: after the005disposition above, the owner explicitly requested006manual restart. Actual006/007are now described in their separate numeric exports and provenance; this does not relabel005startup failure or create005trials.
