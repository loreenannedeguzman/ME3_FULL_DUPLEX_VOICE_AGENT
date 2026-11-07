# Artifact workspace execution — WB-09

The Pi assistant has opt-in tools to list, read, create and edit files in
`data/agent-workspace`, execute Python, and run Python unittest discovery. These
are real discovered stdio MCP tools, used by the existing Router and workflow.
The default `config/assistant.json` and accepted voice entrypoints are unchanged.

Use `config/assistant-workspace.json` with an exact-source/config-bound normal or
typed session grant that explicitly contains `"workspace_access": true`.
Existing provider, expiry, source, request and action budgets still apply.
Without that grant, file writes/execution are absent from model declarations and
direct RPC dispatch is rejected. Typed mode still disallows external actions.
Reversible artifact creation/editing is authorized by the user's current task;
this flag never authorizes messaging, installs, service changes or shopping writes.

The administrator owns workspace/root configuration; model arguments cannot select
a host root. Files use relative paths, POSIX directory handles and no-follow opens.
Links, device files, hidden paths, traversal and non-text extensions are rejected.
Creation requires `NEW`; replacement requires the current SHA256 version. Replaced
bytes are preserved under `.versions` and each write is checked by a fresh read.
Limits: 128 files, 1MiB total visible contents, 64KiB per readable file, 6000 UTF-8
bytes per write, six path segments. A larger source file can be split into modules.
There is no model tool for deletion, project source editing or credentials.

`python` and `python_tests` are command IDs, never shell strings. Execution uses the
Pi's existing `/usr/bin/bwrap` and system Python standard library, rather than the
credential-bearing application virtualenv. A snapshot of artifact files is mounted
read-only at `/workspace`; home, project source, `/etc`, credentials and host sockets
are not mounted. A separate network namespace, empty environment, dropped
capabilities, PID namespace and separate session limit access. `/tmp` is an 8MiB
temporary filesystem. CPU2s, address-space256MiB, file1MiB, descriptors32,
processes8, wall1–3s and combined output1500bytes are bounded. Native namespaces
share the kernel: this is not a VM or a claim against kernel vulnerabilities.
Namespace or platform failure has no unisolated fallback. Windows execution and
file writes fail closed; actual target tests run on the Pi.

Commands cannot persist changes in the mounted snapshot. Generate source artifacts
with the file tools; programs can read their inputs, use temporary files and return
results through stdout/stderr. A run archives its inputs and bounded result receipt
under `data/agent-execution/<run_id>` outside the sandbox. The archive stops at32
runs; an administrator must retain/export evidence before authorizing maintenance.
No tool resets that limit. Timeouts, nonzero exits, output limits and cancellation
are distinct outcomes. Cancellation kills/reaps the isolated process and preserves
its receipt. Previously completed file writes remain in place.

The representative game test uses a deterministic **fake reasoning model** with
real Router, MCP, files, Bubblewrap, Python execution and tests. It observes a real
failed assertion, edits the version it created, reruns tests and plays the game.
This proves the execution path, not hosted-model planning quality or acoustic
interaction. Hosted free-form task selection and voice/text continuation remain
separate acceptance work. No earlier benchmark result applies to this candidate.

An artifact edit invalidates prior workspace read/test results in the router cache.
Earlier results remain in chronological history, but a repeated test command runs
the new snapshot. A nonzero exit, timeout or output limit inside an ordered goal
stops later steps and returns the actual failure to reasoning for correction.

Isolation policy reference: [Bubblewrap's own manual](https://github.com/containers/bubblewrap/blob/main/bwrap.xml).

Required-test policy: python_tests returns failed/nonzero when any discovered test is skipped, including mixed passed/skipped suites. The actual skip count/reason remains in stderr. Repair the missing check/dependency within authorized bounds before claiming the required test run passed.

Execution recovery: workspace_access=true also gates list_workspace_runs and read_workspace_run. They read saved receipts without launching any process. Listings return8run IDs per page, sorted by identifier rather than time. Exact receipt identity, bounded output and saved snapshot hashes are checked. current_workspace_matches=false prevents treating an old passing test as evidence for edited artifacts. Missing/unfinalized/corrupt receipts never imply success. Historical output is untrusted data. No tool deletes archives, resets limits or repeats a run.
