# Reviewer references and original-record provenance

Start with [project labels and the reference map](PROJECT_LABELS.md) for definitions of F1–F8, WB-00–WB-15, requirement IDs and benchmark collection labels. These are planning/evidence identifiers, not grades.

## Diagram assets and review inventory

- [Review manifest](REVIEW_MANIFEST.json): inventory of this arranged submission, excluding the manifest itself.
- [Diagram PNG](ME3_ARCHITECTURE_DETAILED.png) and [SVG](ME3_ARCHITECTURE_DETAILED.svg): rendered/scalable architecture assets. The [combined architecture PDF](<../3 - ME3_ARCHITECTURE.pdf>) contains the diagram and its explanatory second page.

## Cited planning documents

- [PROJECT_BOARD.json](project-planning/PROJECT_BOARD.json): explicit reviewer extract containing work-block goals, acceptance criteria, subblock labels, control rules and finalization phases.
- [PROJECT_WORK_PLAN.md](project-planning/PROJECT_WORK_PLAN.md): original goals/capability section plus reviewer tables generated from that same board snapshot.
- [docs/requirements_registry.json](project-planning/docs/requirements_registry.json): all 44 requirement statements and acceptance/status fields, with extensive historical implementation/test payloads omitted.
- [docs/onit_execution_plan.md](project-planning/docs/onit_execution_plan.md): byte-exact eligible original snapshot, including dated historical entries.
- [docs/measurement_protocol.md](project-planning/docs/measurement_protocol.md): byte-exact historical preparation protocol. Its older course pin is superseded for actual collection by [the final benchmark method](../Benchmarks/METHOD.md).
- [docs/agreed_benchmark_plan.md](project-planning/docs/agreed_benchmark_plan.md): byte-exact historical plan linked from the execution plan; its old course pin is likewise historical.
- [Source and usage snapshot](SOURCE_AND_USAGE_SNAPSHOT.md): publication-safe identities and final observed accounting counters, with Pi and laptop lineages separated.

The [planning publication manifest](project-planning/PUBLICATION_MANIFEST.json) records original and included hashes and distinguishes extracts from byte-exact copies. Extracts are labelled in their own bodies; they are not operational authority or replacements for the original private files. Omitted history is not claimed as reviewed or passed. Raw private evidence paths in historical records are provenance pointers, not promised bundled attachments.

## Retained original-record identities

These identities reflect the laptop originals after the final006/007 reporting amendment. The declared timing selections are recorded; F7 remains partial. No commit or push is authorized before owner file review.

AGENTS.md is copied byte-for-byte. Its known new reporting delta passed the targeted privacy screen; previously reviewed bytes were preserved. AGENT_LOG.md, DECISIONS.md, EXAM_NOTES.md and EXPERIMENT_LOG.md remain withheld because their earlier screen found sensitive or credential-like content. Their full bodies were not rescanned or copied for this refresh; historical finding counts are labelled as such.

[ORIGINALS_MANIFEST.json](ORIGINALS_MANIFEST.json) gives current original hashes and lengths, previous identities and actual final-record receipt hashes. No detected credential, address or phone value is included. Originals remain in private custody; publication copying did not modify them. No original was silently redacted and labelled unchanged. The [engineering history](<../5 - ME3_ENGINEERING_HISTORY.md>) provides the separate narrative supported by those retained records. The unchanged [AGENTS.md](AGENTS.md) is an archived engineering instruction record; its quoted planning documents are mapped above for reviewers, not instructions to run or modify this publication.
