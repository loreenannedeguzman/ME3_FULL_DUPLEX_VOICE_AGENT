# Reviewer extract of PROJECT_WORK_PLAN.md

> Reviewer extract, not a byte-exact original or runtime authority. The original goals/capability section below is retained; the lengthy historical verification narrative and operational details are omitted. The following phase/work-block tables are derived from the same board snapshot. No original is modified.

# ME3 project work plan and management workflow

Updated: 2026-11-07T08:46:45.201578+08:00 (Asia/Manila).

**Engineering: ENGINEERING_ACTIVE. Overall: PARTIAL_PASS. Next work: WB-13.2.**

This plan controls execution of the existing assignment and the user-defined full capability scope. A management update does not authorize resuming a user pause, create a new architecture audit, or replace the actual source/resource/evidence ledgers.

`PROJECT_BOARD.json` is the editable management state. This file is its generated human-readable plan. Use stable work IDs throughout code changes, tests, live scenarios, evidence and project records.

## Goals and completion boundary

- Run a general-purpose voice agent on Raspberry Pi 5 using Pipecat and MCP, with student-built ASR/TTS MCP services hosted on the Pi and on-device VAD. Hosted commercial models are permitted.
- Converse naturally and preserve user participation while listening, reasoning and speaking: context, pauses, hesitation, acknowledgments, corrections, interruptions and replacement intent.
- Discover real tools from their descriptions/schemas, execute authorized goals across applications/services, verify important outcomes and report partial or failed results honestly.
- Qualify real functionality before the accepted freeze and final standardized evaluation. Preserve reproducibility, model-suitability evidence and student handover material.

| User capability area | Work blocks |
| --- | --- |
| 1. General conversation, knowledge, explanations, summarization, comparisons and research | WB-02, WB-04 |
| 2. Music/media search, playback, queues/playlists and supported streaming | WB-05 |
| 3. Messaging, contacts, permitted inbox use and supported calling | WB-06 |
| 4. Tasks, reminders/calendars, notes/documents/books and day/project planning | WB-07 |
| 5. Smart-home/environment controls and sensor/device readings | WB-08 |
| 6. Approved computer/software/development operations | WB-09 |
| 7. Current information, comparisons and explicitly configured monitoring | WB-10 |
| 8. Multi-step, cross-service user goals with progress and recovery | WB-03, WB-11 |

All eight areas remain in the intended scope. An unavailable prerequisite or user deferral stays visible and is not counted as a pass. Only a recorded user-approved scope amendment changes the completion obligations. A scoped available-capability demonstration is labelled separately from full project completion.


## Recorded work-block index

| ID | Meaning | Recorded status |
|---|---|---|
| WB-00 | Project workflow and evidence control | ACCEPTED |
| WB-01 | Reliable normal-assistant execution and recovery | PARTIAL |
| WB-02 | Natural conversation and full-duplex interaction | PARTIAL |
| WB-03 | General-purpose goal reasoning, shared context and durable execution | PARTIAL |
| WB-04 | General knowledge, explanations and research | PARTIAL |
| WB-05 | Music and media | ACCEPTED |
| WB-06 | Messaging and communication | ACCEPTED |
| WB-07 | Productivity, alarms, calendars and documents | IN_PROGRESS |
| WB-08 | Smart-home and environmental control | DEFERRED_BY_USER |
| WB-09 | Computer, software and development tasks | PARTIAL |
| WB-10 | Information retrieval and configured monitoring | PARTIAL |
| WB-11 | Integrated functional qualification across the supported scope | VERIFIED_SCOPED |
| WB-12 | Qualified freeze and reproducibility | VERIFIED_SCOPED |
| WB-13 | Standardized evaluation and final benchmark | PARTIAL |
| WB-14 | Documentation, model suitability and student handover | PARTIAL |
| WB-15 | Completion review and transition to normal operation | PLANNED |

## Finalization phase index

| Phase | Meaning | Mapped work blocks | Recorded status |
|---|---|---|---|
| F1 | Natural speech and conversational presentation | WB-02.4, WB-03.14 | PARTIAL |
| F2 | Pause, directed speech and overlap behavior | WB-02.1, WB-02.2, WB-02.3, WB-02.6 | PARTIAL |
| F3 | Finish remaining functional gaps | WB-01, WB-03.7, WB-03.8, WB-04, WB-07.3, WB-07.4, WB-07.5, WB-09, WB-10 | PARTIAL |
| F4 | Benchmark bridge and method preparation | WB-13.5, WB-13.1 | VERIFIED_SCOPED |
| F5 | Integrated functional qualification | WB-11 | ACCEPTED |
| F6 | Qualified freeze and reproducibility | WB-12 | ACCEPTED |
| F7 | Authorized benchmark collection and analysis | WB-13.2, WB-13.3, WB-13.4 | PARTIAL |
| F8 | Documentation, GitHub submission and completion | WB-14, WB-15 | Not recorded |

These recorded statuses have different dates and scopes. TESTED and VERIFIED_SCOPED do not mean every master requirement or benchmark passed. Current benchmark results and supported-scope acceptance are explained in [the product report](<../../2 - ME3_PRODUCT_REPORT.md>) and [benchmark results](../../Benchmarks/BENCHMARK_RESULTS.md).
