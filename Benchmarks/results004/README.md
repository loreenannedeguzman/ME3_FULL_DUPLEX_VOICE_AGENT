# Cohort004 numeric exports

These three files are byte-exact copies of the offline analysis exports. They contain pinned public dataset prompts and numerical observations, not owner transcripts, generated private replies, provider logs or audio.

Collection004 stopped after 30 stage executions and the first behaviour execution encountered an adapter infrastructure fault. Of the 40 queue items, 30 are done, one failed and nine pending; the 44 execution slots contain 30 scored timing cases, one infrastructure error and 13 unstarted slots. A completed stage session does not imply complete/audio-heard response. Per-metric observed populations and missingness are in [analysis.json](analysis.json); no latency is imputed for missing replies.

See [summary.md](summary.md) and the full [trial ledger](trials.csv). Judge-dependent metrics are NOT_SCORED (zero judge requests); physical audible latency and semantic detection precision/recall are NOT_MEASURED. The analysis status remains root-review-required, with no whole-benchmark/project pass claim. Collection003 remains failed historical evidence with no transferred measurements.

Collection005 was a separately qualified and frozen behaviour-only14-slot successor; it stopped during normal ASR preparation before its first trial, with zero completed executions and no eligible scoring data. See its [failure-only disposition](../results005/README.md). No005 behaviour result is combined with004. Adapter004 code/manifests and these three original numeric exports remain unchanged.

The private offline scorer receipt is identified by SHA256 `d0b3d83e6a8bbf1e8655aa9676195960ffde6bcd97266608c727422ff3d092e2`; it is not copied here. This package contains no active authority or reusable provider grant.
