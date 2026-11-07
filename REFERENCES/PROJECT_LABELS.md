# Project labels and reference map

F means a finalization phase. WB means a work block in the broader project plan. They overlap but are not interchangeable: for example, F5 maps to WB-11, while WB-09 covers computer/software tasks. `WB09` and `WB-09` refer to the same work block. Decimal suffixes, such as WB-09.1, identify subblocks. Numbered owner/engineering records such as 441 are evidence identifiers, not scores or versions of the whole product.

## Finalization phases

| Label | Meaning | Related work blocks |
|---|---|---|
| F1 | Natural speech and conversational presentation | WB-02.4, WB-03.14 |
| F2 | Pause, directed speech and overlap behavior | WB-02.1, WB-02.2, WB-02.3, WB-02.6 |
| F3 | Finish remaining functional gaps | WB-01, WB-03.7, WB-03.8, WB-04, WB-07.3, WB-07.4, WB-07.5, WB-09, WB-10 |
| F4 | Benchmark bridge and method preparation | WB-13.5, WB-13.1 |
| F5 | Integrated functional qualification | WB-11 |
| F6 | Qualified freeze and reproducibility | WB-12 |
| F7 | Authorized benchmark collection and analysis | WB-13.2, WB-13.3, WB-13.4 |
| F8 | Documentation, GitHub submission and completion | WB-14, WB-15 |

## Work blocks

| Label | Meaning |
|---|---|
| WB-00 | Project workflow and evidence control |
| WB-01 | Reliable normal-assistant execution and recovery |
| WB-02 | Natural conversation and full-duplex interaction |
| WB-03 | General-purpose goal reasoning, shared context and durable execution |
| WB-04 | General knowledge, explanations and research |
| WB-05 | Music and media |
| WB-06 | Messaging and communication |
| WB-07 | Productivity, alarms, calendars and documents |
| WB-08 | Smart-home and environmental control |
| WB-09 | Computer, software and development tasks |
| WB-10 | Information retrieval and configured monitoring |
| WB-11 | Integrated functional qualification across the supported scope |
| WB-12 | Qualified freeze and reproducibility |
| WB-13 | Standardized evaluation and final benchmark |
| WB-14 | Documentation, model suitability and student handover |
| WB-15 | Completion review and transition to normal operation |

## Reading the evidence

- R1–R9 and M01–M35 are requirement IDs from the registry; their exact statements and acceptance conditions are in [the registry extract](project-planning/docs/requirements_registry.json).
- ACCEPTED, TESTED, VERIFIED_SCOPED, PARTIAL, BLOCKED and DEFERRED describe the recorded scope; they are not interchangeable. See the board extract for its original status definitions. Historical statuses are retained, not upgraded by this publication.
- Collection003–007 are separately recorded benchmark attempts/continuations, not repeated passes or source versions. See [the benchmark report](../Benchmarks/BENCHMARK_RESULTS.md).
- A source-manifest digest identifies source contents; it is not a Git commit. The Pi and independent laptop have different source identities and ledgers.
- A planning document, software test, digital audio result and owner live observation establish different kinds of evidence. A planning reference does not establish that the referenced test passed.

## Where to find the cited documents

| Citation in an engineering record | Included reviewer reference |
|---|---|
| PROJECT_BOARD.json | [Selected board fields, work blocks and phase criteria](project-planning/PROJECT_BOARD.json) |
| PROJECT_WORK_PLAN.md | [Goals and generated reviewer tables](project-planning/PROJECT_WORK_PLAN.md) |
| docs/requirements_registry.json | [All 44 requirements and acceptance fields](project-planning/docs/requirements_registry.json) |
| docs/onit_execution_plan.md | [Byte-exact execution plan snapshot](project-planning/docs/onit_execution_plan.md) |
| docs/measurement_protocol.md | [Byte-exact historical preparation protocol](project-planning/docs/measurement_protocol.md) |
| Current source/resource ledgers | [Publication-safe identity and usage snapshot](SOURCE_AND_USAGE_SNAPSHOT.md) |

The historical measurement protocol names an older course pin. The current, actually used method and updated pin are in [Benchmarks/METHOD.md](../Benchmarks/METHOD.md); the historical draft is retained for traceability, not presented as the final collection configuration. Likewise, earlier headings in the execution plan are dated history; appended updates and the final reports explain the later disposition. Raw evidence paths mentioned in historical records identify privately retained material; they do not imply those private files are bundled.
