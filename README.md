# ME3 Full-Duplex Voice Agent

Loreen Anne P. De Guzman · AI 231 · Student number 202521201

**Owner review folder. No commit or push until approved.**

ME3 runs on a Raspberry Pi 5 using Pipecat, student-built speech MCP services, Onit, shared voice/text context and guarded tools. Speech and typed text are both valid inputs. Typing can support unclear or unrecognized speech without starting a separate task context. Normal operation needs Wi-Fi and privately configured cloud services, with no laptop/Codex desktop runtime process.

Spanish drafting/translation and contextual spoken replies were demonstrated without a language button. Output uses separate English and female Spanish Piper voices. Recognition remains English-configured; Spanish input may need typed clarification. This is finite demonstrated scope, not a formal multilingual accuracy or voice-quality qualification. See [the Spanish capability section](<2 - ME3_PRODUCT_REPORT.md#spanish-translation-and-spoken-output>).

## Numbered reading order

- [1. Presentation summary](<1 - ME3_PRESENTATION_SUMMARY.pdf>): Two pages: architecture, models, measured results, text input, Spanish scope and 14 configured task servers.
- [2. Product report](<2 - ME3_PRODUCT_REPORT.md>): Capabilities, implementation, evidence and limitations.
- [3. Architecture](<3 - ME3_ARCHITECTURE.pdf>): Diagram on page 1; explanatory guide on page 2.
- [4. GUI voice/text guide](<4 - ME3_GUI_VOICE_TEXT_GUIDE.md>): Controls, captured text, typed corrections, Copy conversation, Spanish output and demo preparation.
- [5. Engineering history](<5 - ME3_ENGINEERING_HISTORY.md>): Development decisions, failures and finite qualification evidence.

See [submission contents](SUBMISSION_CONTENTS.md) for the complete arranged review tree.

## Three folders

- [Benchmarks](Benchmarks/README.md), including [actual results and missingness](Benchmarks/BENCHMARK_RESULTS.md).
- [ME3Deployment](ME3Deployment/README.md): source, setup/operation, dependencies and provenance.
- [REFERENCES](REFERENCES/README.md): planning records/extracts, glossary, [Mermaid source](REFERENCES/ME3_ARCHITECTURE_DETAILED.mmd), PNG/SVG assets and [review inventory](REFERENCES/REVIEW_MANIFEST.json).

## Evidence boundaries

The declared benchmark selection has 30 stage timing records and 14 disjoint behavior timing records. These are not 44 passes or one uninterrupted run. Missing/late replies and unavailable required measurements keep F7 PARTIAL. The final supported functional scope was owner-accepted, including a verified WhatsApp submission/automatic close; a subsequent redundant-close shutdown remains a retained limitation. No whole-project or all-master pass is claimed.

Pi source-manifest identity: `03d7a144917edb920949c1ebaca116e277cdb55ae5c2589018334f14d522721b`. This is not a Git commit. The sanitized deployment copy discloses differences and is not byte-identical to the private measured installation. Configured task servers do not imply blanket permission or successful live qualification of every operation.

Presentation scripts remain in the separate local study folder. Credentials, account state, private conversations/contacts/media, environments, raw recordings and model weights are excluded. Four sensitive original log documents remain privately retained; eligible reference originals and labelled extracts disclose their provenance. No project-wide redistribution license is assumed.

Intended repository: https://github.com/loreenannedeguzman/ME3_FULL_DUPLEX_VOICE_AGENT
