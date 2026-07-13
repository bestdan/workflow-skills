---
title: Port the read-only reporters — status, classify-exit, exit-reason
priority: medium
size: 5
status: ready
created: 2026-07-13
expires: 2026-12-31
source_branch: bestdan/port-orchestrator-to-python
parent: orch_py
is_blocked_by: orch_py_task_4
related_files:
  - scripts/spawn-orchestrator.sh:303 # status
  - scripts/spawn-orchestrator.sh:106 # classify-exit
  - scripts/spawn-orchestrator.sh:119 # exit-reason
  - scripts/test-spawn-orchestrator.sh:925 # status
  - scripts/test-spawn-orchestrator.sh:1117 # classify-exit
tags: [orchestrator, python, port]
---

← [[orch_py_plan]]

## Context

Three read-only-ish subcommands that inspect run state and report on it:

- `status` (180 lines) — one-shot report of a run's live state (RUN.md, ledger, heartbeat).
- `classify-exit` (73 lines) — maps an exit code + log tail to a run outcome
  (`continuing` / `paused` / `done` / `systemic` / `deadline`), no model call.
- `exit-reason` (69 lines) — the exit contract: the orchestrator *declares* why it stopped.

These are the natural next port: they parse files and emit structured output, which is the
work Python does best and bash does worst. `classify-exit` and `exit-reason` are also the
decision functions the supervisor's teardown-vs-relaunch logic reads, so their behavior is
load-bearing even though they mutate little.

**Check the caller before porting `classify-exit`.** The generated launch script performs its
own exit classification inline (`test-spawn-orchestrator.sh:1320`) — confirm whether it
invokes the `classify-exit` subcommand or duplicates the logic. If it *invokes* it, then
`classify-exit` is **Tier B** (reachable from the launchd wake loop) and must be deferred to
task 8 rather than ported here. Resolve this first; it changes this task's scope.

## Task

- Implement `status`, `exit-reason`, and (if and only if the check above clears it)
  `classify-exit` in `scripts/orchestrator/report.py`.
- Reproduce output byte-for-byte, including the exit-code contract for `classify-exit`
  (callers branch on both stdout and rc).
- Preserve `exit-reason`'s fail-closed posture: an undetermined signal must never green-light
  a destructive action (the same D2 posture documented around `spawn-orchestrator.sh:5948`).
- Add the cleared subcommands to `PORTED`; delete the corresponding bash.

## Acceptance Criteria

**Code-enforced:**

- Golden corpus reproduces byte-for-byte for all ported subcommands, across every
  classification branch (`continuing`, `paused`, `done`, `systemic`, `deadline`).
- `bash scripts/test-spawn-orchestrator.sh` passes unchanged — sections at 925 (status),
  1016 (assert-run-head), 1117 (classify-exit), and 1213 (supervisor-check, which consumes
  the classification).
- If `classify-exit` was deferred to task 8, this task's PR says so explicitly and the
  plan's task 8 is updated to include it.
- `just check` green.
