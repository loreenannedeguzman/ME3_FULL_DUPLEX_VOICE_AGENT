# ME3 benchmark results — partial 004 and retained stopped attempts

Reader reference: [phase/work-block glossary and cited planning documents](../REFERENCES/PROJECT_LABELS.md). F7 is the benchmark phase; collection numbers identify separate recorded attempts or continuations.

**Language boundary:** Spanish drafting/translation and female Spanish speech have separate owner demonstrations described in the [product report](<../2 - ME3_PRODUCT_REPORT.md#spanish-translation-and-spoken-output>). This fixed stage corpus does not benchmark Spanish recognition, translation accuracy or Spanish voice naturalness; its WER must not be presented as a Spanish or universal multilingual score.

**Published benchmark report - 7 November 2026.**

Collection004 completed its 30 stage trials, stopped on its first behavior execution and has completed local scoring of the available stage records. **F7 is not complete.** The results below preserve missingness and the diagnosed adapter failure. Collection003 is separately **STOPPED**, not a completed benchmark. It provides retained diagnostic observations, not usable aggregate agent-performance metrics. The first raw label was “no reply” within the unchanged capture window; retained Pi trace review shows that complete response PCM arrived after that window. This is a late response, not evidence of broken wire or no generated answer. The later readiness/cleanup failure remains a separate adapter investigation.

## Frozen identity and plan

| Item | Exact identity / declared capacity |
|---|---|
| Measured Pi source | `03d7a144917edb920949c1ebaca116e277cdb55ae5c2589018334f14d522721b` — 586 source / 64 native entries |
| Course commit | `57eaddbc188ce9a19b8fd002b575f703426956f4` |
| F6 freeze manifest | `45c0d2f64df256aac2fa0c221abeb8e94f78d3b53da3a9f7eb8065ef66a1d43c` |
| Collection003 contract | `703070cc87d4bcf9ca884edb03dad0c33345f511ee6ae66cbbecd8dbc2aa00e5` |
| Plan | Seed 0; 10 stage phrases × 3 runs; one behavior round |
| Queue / execution capacity | 40 queue items / 44 execution slots, including paired/main executions |
| Local transcription ceiling | 52; not an observed call count or result count |
| Route | Digital direct rig over the declared loopback/reverse-SSH transport; no physical audio |

The source/adapter freeze remains a scoped F6 result. It is not invalidated or expanded into a full-suite reinstall claim by this collection failure. Source-manifest hashes are not Git commits.

## Separate adapter004 qualification and collection status

The normal-source freeze above remains unchanged. Adapter004 has a separate freeze addendum, SHA256 `3c35d27b09bec7916c7ff37d02250305be26acb45cbf12859e5cc98081f26e6b`, binding adapter manifest `378e96f2cf44a962047fb4633b7ca4b1b5c8b982cacdbf76fcf0a0c6c9a4a24b` and contract `0494e2928e1f6aabde7e2bb986cde77bad28fb3bbc19f728667c22d16b4f8fbb`.

One focused actual-Pi cleanup case passed after two earlier retained fixture stops. Three controlled conditions used the actual current transport lifecycle:

| Condition | Observed settlement | Result |
|---|---|---|
| Baseline socket default / same 2-second guard | 2.017 s | UNVERIFIED |
| Public connection close timeout 1 second / same 2-second guard | 1.014 s | VERIFIED |
| Resistant peer / unchanged guard | 2.017 s | UNVERIFIED, correctly refused |

Qualification receipt SHA256 is `93bc65e789bdee31b2b623a1e4ed5d9a24f8b3dfc78d485abfd24036c60af872`. These are controlled cleanup observations, not three benchmark samples or a mean/p95. Source/private/counter/GUI state was preserved; no provider/device/benchmark trial ran. Seven prior checks plus the earlier one-case scope were explicitly reused rather than rerun. The exact historical pending-disconnect location remains unproved.

## Collection004 — actual partial results

Collection004 is **STOPPED**, with final cleanup and normal-evidence export verified. The scoped source/models/reply windows/seed/sample plan remain unchanged. The local-only scorer002 completed without scoring errors, network/provider calls, production mutation or raw-input changes. Its status is `BENCHMARK441_PARTIAL_LOCAL_SCORING_REVIEW_REQUIRED`; analysis is `POSTSCORING004_ANALYSIS_REVIEW_REQUIRED`. Neither status is overall acceptance.

| Population | Observed state |
|---|---|
| Stage trials | 10 phrases × 3 runs = **30** scored timing records |
| Queue items | 30 done / 1 failed / 9 pending, out of 40 |
| All execution slots | 30 SCORED_TIMING / 1 INFRASTRUCTURE_ERROR / **13 UNSTARTED**, out of 44 |
| Course audio no-reply labels | **12 of 30 stage trials**; retained, not replaced or silently excluded |
| First behavior execution | `r01_v1_interrupt_v1_interrupt_120`, infrastructure error |
| Local transcription | 30 actual attempts; frozen ceiling 52 |
| Judge scoring calls | 0; NOT_SCORED_JUDGE_READINESS_TIMEOUT |

A stage queue `done`/SCORED_TIMING record is not a passed answer, complete substantive reply or behavior success. Nine pending queue entries expand to 13 unstarted execution slots because some have paired/clean executions. No favorable replacement, automatic retry or merging with003 occurred.

### Stage timings and missingness

All units are seconds, rounded to four decimals. Each row starts from the same **30-stage-trial population**. Statistics use only observed values; missing observations are neither zero-imputed nor counted as successes.

| Course metric | Observed N | Missing / 30 | Mean | P50 | p95 | Course median 95% CI |
|---|---:|---:|---:|---:|---:|---|
| Producer-mark E2E (`e2e`) | 21 | 9 | 6.4393 | 6.4977 | 7.3566 | [6.0265, 6.7266] |
| Detected-audio E2E (`e2e_heard`) | 18 | 12 | 7.2858 | 7.3480 | 8.0136 | [7.0280, 7.5400] |
| Endpointing | 30 | 0 | 0.6669 | 0.6355 | 0.6912 | [0.6199, 0.6532] |
| ASR final | 30 | 0 | 0.7903 | 0.6018 | 1.3155 | [0.5682, 0.7278] |
| LLM first text | 24 | 6 | 4.7732 | 4.7321 | 6.1688 | [4.2637, 5.0702] |
| TTS first PCM | 21 | 9 | 0.5065 | 0.5459 | 0.6944 | [0.3854, 0.6070] |

`e2e_heard` is the upstream name for digital audio detection: it is **not physical acoustic audibility**. The three producer-mark E2E observations without detected-audio E2E explain why N=21 and N=18 differ. Twelve course no-reply labels do not mean 12 missing producer metrics. Raw stage onsets do not independently establish meaningful or complete replies; a filler is not relabelled as substantive content. Stage timing components have different observed populations and boundaries, so their separate means are not an additive end-to-end budget.

The course median intervals use its pinned **1,000 iid-trial bootstrap, seed 0, N≥3**. Repeated trials are not independent phrases; separate supplemental whole-phrase-cluster bootstrap intervals use 1,000 samples and seed 0. For the eight observed phrases, supplemental median intervals are [6.0265, 6.6023] for producer E2E and [7.1720, 7.4600] for detected-audio E2E. These sensitivity intervals do not replace upstream results or impute missing trials. In particular, N=18 makes detected-audio p95 close to the slowest observed trial; it is not a stable population-tail estimate. Missingness may bias every observed-only latency summary. No unconditional target pass or overall quality grade is inferred.

### WER and local scoring

The 30 fixed stage trials contain **198 reference words and 35 edits**: word-weighted pooled WER is **17.68%** (35/198); unweighted mean-phrase WER is **20.03%**. They are different aggregations. The scope is this public stage corpus, not universal accent/noise/multilingual performance. CER is not reported.

Scoring used `Systran/faster-whisper-small`, revision `536b0662742c02347bc0e980a01041f333bce120`, CPU/int8/two threads, 30 local transcription attempts. This analysis model is distinct from the normal Groq Whisper recognition path. Local scorer manifest is `11740a511f4fe2a5c1498297a8d84e955cb8716f71a1cfe66adbb89c36f9f657`; scoring ran with network denied and zero provider requests. Raw collection inputs were unchanged.

### First behavior failure and separate005 cohort

The normal generation-1 output buffer was intentionally **SUSPENDED**, holding **5,760 bytes / 120 ms**. The digital adapter's blanket two-second BaseOutput writer timeout expired before the cut-in ended. The wire pump continued on its 20 ms cadence through 06:16:32 with no wire error. This identifies a **benchmark-adapter fault**, not a demonstrated normal-production interruption failure or semantic model error. Final operator cleanup and evidence export were verified; these do not turn the failed execution into a pass.

Adapter005 subsequently passed **one focused actual-Pi case with nine assertions**, not nine tests. Qualification receipt SHA256 is `027c5b76618987b75ae8a63d3d47e94e04fd841048efd5530bfda61a29bbb6d8`. Its separate freeze manifest is `f637ac343ab39150f9244dc4fe8a39461c93bcde720bb5daa41596c6f1b0ebb0`, with contract `e5ebda1e3e4cf2857ad03ddc4a335cdf49d0075ee22744efcbbb7754a2107418`. The authorized collection005 subsequently **STOPPED before its first trial input**. The Pi exception ends at `prepare_speech_worker` / `actual_asr_worker_readiness_failed`; its normal event records identify `stage=dns`, `error_type=TimeoutError`, `automatic_retry=False` and `audio_opened=False`. Startup credentials also recorded `startup_credentials_failed_no_retry`, with **zero inference requests**. Utility/ASR/TTS MCP connections preceded this external-readiness failure; connected MCP did not mean ASR-ready. This is a bounded startup DNS-readiness timeout, not proof of a global provider outage or normal semantic failure.

The Pi receipt records **0 completed executions / maximum14** and no completed sessions; the root receipt records STOPPED / TimeoutExpired. Cleanup and normal-evidence export are verified, with the owned tunnel reaped. The initialization-only export contains three files / 3,605 bytes, archive SHA256 `3b1cc9de7ad405a1fe05c3fc1ec90c2d7395044b1573759000294842639a06e8`. Usage remains **1049 ASR / 2065 reasoning / 2453 TTS / 2 commit_item**. No audio/input trial occurred and scoring005 is **NOT_RUN — no eligible data**. Queue/trial outcomes are not invented from the execution ceiling. No automatic retry of005 occurred; separately authorized006 is retained below, and 007 subsequently completed only its previously unstarted slots. This adapter-only successor leaves normal source/models/windows unchanged; its single qualification case is not nine tests or full behavior acceptance.003,004 and 005 remain separate.

### Collection004 resource and context population

The existing local scoring output includes the remote Pi resource stream. These are **full stopped-run** observations, not just successful response intervals or the 30 stage windows. Pinned course `fdbench/probe.py` samples procfs approximately once per second; `fdbench/score/resources.py` summarizes non-null values, retaining missingness. The 904 stored samples span **1,003.3701 seconds**, with gaps/session boundaries; statistics are sample-weighted, not time-weighted. No new measurement was run to obtain this table.

| Recorded quantity | Population | Actual result / definition |
|---|---|---|
| Whole-machine CPU utilization | 874 observed / 904 samples, 30 missing | Mean **11.7846%**, P50 **2.50%**, p95 **44.95%**, max **83.50%**; mean utilization across all four cores, 100% means the whole machine busy |
| System available memory | 904 / 904 samples | Minimum **5,251.3 MiB**; reported total **8,058.3 MiB**; procfs MemAvailable, not exclusive assistant allocation |
| Swap used | 904 / 904 samples | Observed max **0.0 MiB** |
| Temperature | 904 / 904 samples | Median **47.95 °C**, max **53.45 °C**; sampled sensor values, not universal thermal limits |
| Current throttle flags | 904 sampled readings | No current throttle/undervoltage/frequency-cap/soft-temperature flag observed; no new since-boot flag transition recorded |
| Prompt/context tokens for eligible answering LLM calls | 20 calls with usage reports | Median **4,990**, max **4,993**, first **4,989**, last **4,985**; message counts null/unavailable |

The upstream fields named `_mb` divide procfs KiB by 1,024 (and resident pages by 2²⁰), so memory is reported here as **MiB**, not decimal MB. Global CPU includes the monitoring workload and other Pi processes; it is not assistant-only CPU. Unobserved instants and initialization values are not filled. Available RAM is not the application's resident set, and process-labelled RSS sums are not presented as exclusive memory or an all-process peak bound. No power/energy measurement exists.

The context statistic selects calls using the pinned `score/stages.py` answering-request rules and prefers reported prompt-token usage. All20 values have source `usage`; they are not locally estimated tokens or total input-plus-output billing. This is a sample of calls, not 20 successful tasks or a long-context retention test. Independent fresh trial contexts mean that first/last token counts do not measure durable conversational growth.

## Collection006 — actual partial behavior scores

006 is **STOPPED / partial**, not a stage repeat. Contract `5482c29a07d76b2e5cdfc5f0d9b56827541c0eb51a219af76b0bc1fd576d5670` retains normal source 03d7 and course 57e. Its queue has **7 done / 3 pending** items; per-execution inventory is **9 SCORED_TIMING / 5 UNSTARTED** out of 14 slots. Five of the nine recorded executions retain course no-reply labels within their original windows. SCORED_TIMING does not mean a successful substantive answer. No 006 stage timing/WER sample exists and its behavior results are not pooled with 004's 30 stage trials.

### Final offline course results

| Metric | Final numerator / denominator | Value | Supplemental Wilson 95% interval |
|---|---:|---:|---|
| False turn-taking, combined |1 /2 |50% |[9.45%,90.55%] |
| Side-talk false turn-taking |1 /1 |100% |[20.65%,100%] |
| TV/radio false turn-taking |0 /1 |0% |[0%,79.35%] |
| Synthetic-pause TOR |0 /1 |0% |[0%,79.35%] |
| Turn-taking TOR |0 /1 |0% |[0%,79.35%] |
| Turn-taking missed rate |1 /1 |100% |[20.65%,100%] |

Intervals are supplemental two-sided Wilson 95% outputs; course numerators/denominators are unchanged. These tiny samples are not broad operating estimates. The v1 interruption case has **one overlap not played** and no eligible stop/response latency; Candor-pause has N=0. All judge-dependent interruption/respond/resume semantics remain **NOT_SCORED**, with zero judge calls. Physical audibility and independent detection precision/recall remain **NOT_MEASURED**. Raw cancellation/interruption marks are not ground-truth labels.

The side-talk record contains approximately 0.909s of detected audio. Its provisional live “silent” indication changed under final offline word-count scoring to false turn-taking1/1. This report uses the **final scored values**, not the earlier glyph or provisional TOR. The original audio/timing record remains unchanged; no miss is relabelled as success. Scorer004 completed 9 local CPU/int8 transcription attempts (ceiling 22), zero provider calls, network denied, raw inputs unchanged and no scoring error. Collection receipt SHA256 is `a59e69b002b75e04101fb7edb05685d1d0796fc07f65e8de3e62defcb49ac9ec`; local scoring receipt is `c7df4ee9535be818065343b6e8cc0afc19b573b4d170d0b209c7a4b74d945d75`, and results are `8a3f3c901cf1bf9e1dd3a80fd0b0af824d0dcf6c106282a11a200cfacb0b27be`.

### Why006 stopped

Eight execution sessions completed verified exact peer-end/reset and cleanup. The ninth produced valid trial timing, but the exact upstream runner entered its keyboard-wait branch after five consecutive reply-expected records had empty bot segments. The noninteractive launch had no keyboard watcher and `--no-live` hid the prompt. This blocked before the next guarded trial/reset. Queue item 7 was already done; no item 8 trial start exists.

The normal ninth session delivered its late answer at 08:09:43.101, drained its tail by 08:09:43.295 and remained listening until its 180-second cap at 08:12:04.560. It had **zero pipeline faults**, verified cleanup, settled receiver and zero unsettled jobs. Its missing peer-end acknowledgement is the consequence of the runner's hidden pause, not the cause of a production pipeline failure. There was no direct keyboard-loop stack capture; the pinned source branch and ordered saved queue/normal-session evidence support this diagnosis. The **five no-reply observations remain genuine window-bounded misses**, even where a later answer exists. Final containment/export were verified. Usage is **1058 ASR /2074 reasoning /2477 TTS /2 commit_item**, with no reset/refund.

## Collection007 — remaining executions completed and scored

007 completed **3/3 queue items**, producing **5/5 SCORED_TIMING records, zero unstarted slots** under contract `f5dfd1b9b03ee5cfc1172a7e8c91610b52a075a57815fe73f4195ec4104fcb46`. Four of five records retain course audio no-reply labels. Completed collection/scoring means evidence was recorded and processed, not a quality pass.

| 007 final observation | Actual result | Interpretation |
|---|---|---|
| Candor-pause TOR |0/1 (0%) |One case; supplemental Wilson 95% interval [0%,79.35%] |
| v15 interruption main |Overlap not played; eligible stop/response-latency N=0 |No barge-in success or latency inferred |
| v15 backchannel main |Overlap not played; eligible stop/response-latency N=0 |No acknowledgment/resume success inferred |
| Paired clean records |Both retain no-reply labels |Not substitutes for successful main-case overlap |
| Judge/physical detection |0 judge calls, semantics NOT_SCORED; physical audibility and precision/recall NOT_MEASURED |Unavailable measures are not zero scores or passes |

Five local CPU/int8 transcription attempts completed under the nine-attempt ceiling; network was denied, provider calls were zero, raw inputs unchanged, score errors empty and production unmodified. Root collection receipt has failure null and verified cleanup/export. Final Pi usage is **1064 ASR /2080 reasoning /2487 TTS /2 commit_item**; independent laptop counters are unchanged. No new runs are planned.

### Coverage, not pooled success

The exact coverage check compares execution IDs, not favorable outcomes:

| Partition | Original queue indices | Unique recorded execution IDs | Remaining slots |
|---|---|---:|---:|
|006 |0–6 (first seven items) |9 |Five assigned to 007 |
|007 |7–9 (last three items) |5 |0 |
| Coverage relation |No ID overlap |All 14 selected IDs recorded |No all-pass inference |

This is **14 recorded executions, not 14 passed tasks or one uninterrupted run**. Raw trials and latencies are not pooled across 006/007;004's 30 stage trials are not imported. No replacement or additional stage run was used. Previous 003/004/005/006 failures and missed-response labels remain retained.

007 differs from 005 in the bounded launcher/cap/plan files; source 03d7 and audio-adapter behavior are unchanged. It has four reply-expected executions, avoiding 006's upstream five-miss headless keyboard pause rather than universally repairing it. A small remaining-item collection is not a general launcher fix.

007 collection receipt SHA256: `caeacf2549d8c843c1e015c7eb8d56be1014ec1e3d8f0289302e2186fb758df2`; scoring receipt: `727c77f8ef3dff0fd418ca97ea70146a1e67343937b2d6a2529cb0395effffaf`; scoring manifest: `a3aced7dc81772e99e51a9462a0d32f84f4f53850d9388fa73767f31fe6468bd`; results: `ae7c3d6b8cb710f157ecc73449cab9df7130776e3d172cb09622fee3cd467a43`. Analysis/coverage are review-required evidence, not overall acceptance.

### Interpretation and statistical limits

Timely-reply performance is **weak**:004 has 12/30 course no-reply labels,006 has 5/9 and 007 has 4/5 in their respective populations. These are separate observations, not a pooled success-rate estimate. Missing timely replies prevent several intended overlaps; judge unavailability alone does not explain away these failures. Owner demonstrations establish different functional scopes and cannot replace them.

Thirty stage trials do not guarantee statistical reliability. Repetition of 10 phrases and one small behavior round limit independence, tail estimates and generalization. No warm-up samples were discarded post hoc, no miss was substituted and no P99 is claimed. Observed-only latencies, small-denominator behavior rates and unavailable measurements stay disclosed. **F7 is PARTIAL; coverage completion is not all-pass benchmark acceptance.**

## Collection003 — observed outcomes and denominators

| Population / record | Actual observed state | Interpretation |
|---|---|---|
| Queue entries | 40 planned: 1 `done`, 1 `failed`, 38 `pending` | Queue bookkeeping, not 1 success / 40 agent tasks |
| Pi executions | **0 completed**, maximum 44 | Do not infer a complete denominator from execution capacity |
| First stage record | `r01_stage_latency_phrase_v15_background_27`: audio files retained; `actual: no reply`, `ok: false`, empty bot segments | Retain the raw observation; complete PCM arrived after capture ended, so no aggregate response-rate estimate is inferred |
| Next queue entry | `r01_stage_latency_phrase_v1_interrupt_174`: `fresh_greeting_tail_listening_barrier_unverified` | Readiness/transport failure; not a scored task answer |
| Pi session failure | `DigitalBridgeError: digital_transport_cleanup_unverified` | Internal session-finalization failure; distinct from subsequent operator cleanup |
| Final operator cleanup | `cleanup_verified: true`; owned tunnel reaped | Verified final containment does not erase the internal exception |
| Evidence export | `normal_evidence_exported: true`; one normal-session evidence archive retained | Pi normal speech was logged; first response is known late, while the subsequent disconnect fault remains unresolved |
| Scoring | Not performed; zero judge calls | No aggregate score is available |

### First-trial diagnostic timing

| Trial-relative observation | Offset |
|---|---|
| User-end mark | 1.534 s |
| First TTS request | 9.3706 s |
| Recorded capture end | 9.792 s |
| First complete PCM available | approximately 9.98 s |

The retained response therefore became available after capture ended under the untouched default window. These single-trial diagnostic offsets are not aggregate TTFT/TTFB, physical audibility or calibrated cross-machine accuracy. The `no reply` label remains in the raw course record; it is not rewritten as a pass.

The separate lifecycle investigation identified a 2-second outer settlement budget versus the socket library's default 10-second close handshake. Adapter004 uses public connection `close_timeout=1` while retaining the 2-second settlement limit. Its focused actual-Pi qualification is recorded above; the exact historical pending-disconnect location remains unproved. That qualification does not turn collection003 into a completed or successful cohort.

The queue advanced after the first raw no-reply observation and stopped on the next entry's readiness error. Neither entry was replaced with a favorable sample. The second error cannot be silently counted as a completed execution, and the first queue `done` marker cannot be presented as a passed trial. Remaining per-execution attempted/unstarted categorization must follow the final adapter investigation; this document does not guess it from 44 minus queue counts.

The final evidence export observed cumulative Pi usage **ASR 1018 / reasoning 2034 / TTS 2312 / commit_item 2**, compared with the frozen baseline **1017 / 2033 / 2308 / 2**. These are retained counter observations, not a reset, refund, workload-average measurement or proof that every captured audio buffer reached the scoring rig. Normal and benchmark resource authorities remain separate.

## What is and is not scored

| Measure | Actual004 report state |
|---|---|
| Stage latency and WER | Available for the explicit populations above; partial-cohort results, not all-pass |
| Pause, turn-taking and false/missed rates |004 eligible behavior N=0; separate 006 final small-sample results above |
| Digital barge-in / acknowledgment continuity | No eligible measured stop/response latency or judge-confirmed continuity result |
| Judge-dependent interruption relevance and semantic behavior | NOT_SCORED for this cohort; zero judge calls |
| Detection precision/recall and false-background-interrupt rates | NOT_MEASURED; no independent frozen opportunity/prediction label mapping |
| Optional UTMOS / voice ratings | UTMOS disabled; no eligible voice rating result |
| Physical acoustic cessation/onset | NOT_MEASURED; digital records are not substitutes |
| Whole assistant functional/privacy/recovery acceptance | Not a course-stage latency/WER score; separate finite engineering and owner scopes |

The preselected Gemini `gemini-3.8-flash` judge had one separately authorized public readiness request that timed out after 34.986999 seconds without an HTTP status. Protected state and normal/benchmark budgets remained unchanged. The original five-call ceiling was unused; this cohort has zero judge scoring calls. NOT_SCORED is not a semantic score of zero, a model switch or permission to retry.

The 30 stage records include raw interruption marks, but those events are not independent semantic ground truth or an eligible behavior cohort. Source software tests, owner acceptance and context-token observations do not substitute for behavior scoring. The historical 31-trial ME3 run and older `106ac8e` preparation are **excluded from both003 and004 denominators**. Failures, cancellations, unknowns, unstarted slots and missing values remain retained.

## Evidence and next gate

Private engineering custody retains the raw files, audio, events and receipts. Public evidence must be selected and privacy-reviewed before inclusion; source paths on the engineering laptop are not runtime dependencies.

| Retained artifact | SHA256 |
|---|---|
| Root collection003 receipt | `a66d25fb872d9091e278e35bc807da8b49c8fbdabedbe5f4c2736620bc9bf18f` |
| Pi collection receipt | `c84ddd7579d6c7e5840d2b4a414dbc8ec5bbf64c17d5c1b4a462a9eaa1768d42` |
| Queue manifest | `68ba43bbe001a9061fe780ad743f63db5ac7e6d9b7125ef2c54534a79b3cb254` |
| Normal-evidence export receipt | `ff3003cfddc893d980c032ee1d1a53001a1d5d75b52485ac7d8fe078a70e266d` |
| Exported normal-evidence archive | `011cb58cd6d57f42db704c7e32dc88c19298cedde45b6e76cc0b4b15d0d59fa5` |

Collection004 local scoring and analysis are complete for its available 30 stage records; the behavior cohort remains incomplete. The following exact artifacts bind these results, while003 remains separately immutable:

| Collection004 artifact | SHA256 |
|---|---|
| Root collection receipt | `c5c29f7f3074de0e22bbd810f2231e11d1bdbf9e2a87cba936f9e448406c2ffb` |
| Local scorer002 receipt | `d0b3d83e6a8bbf1e8655aa9676195960ffde6bcd97266608c727422ff3d092e2` |
| Local scoring results | `eff7b40af0ceff0066d47dd3f0fbef4a35db2acfaab6f694868bafd66f2bd794` |

The analysis JSON, summaries and per-execution CSVs preserve exact precision, trial identities and missingness in private engineering custody. No owner transcripts, generated replies or credentials are exported here. The published report uses 004 stage results, separately scored 006/007 behavior records and retained 003/004/005/006 failures. All 14 selected behavior IDs are recorded in disjoint partitions; no pass or pooled estimate is inferred. 005 remains stopped at startup. No new runs are planned. **F7 remains PARTIAL, not all-pass.** The repository is [ME3_FULL_DUPLEX_VOICE_AGENT](https://github.com/loreenannedeguzman/ME3_FULL_DUPLEX_VOICE_AGENT).
