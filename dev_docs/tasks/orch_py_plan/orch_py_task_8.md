---
title: Decide the launchd boundary — port Tier B, or freeze it in bash
priority: high
size: 5
status: needs_refinement
human_approval_requested: true
# promoter: scope exceeds size 5 — unbounded: under option (a) it is a docs+lint task, under (b)/(c) it ports ~1,000 lines. Split the decision from the implementation
created: 2026-07-13
expires: 2026-12-31
source_branch: bestdan/port-orchestrator-to-python
parent: orch_py
is_blocked_by: orch_py_task_5
related_files:
  - scripts/spawn-orchestrator.sh:157 # supervisor-check
  - scripts/spawn-orchestrator.sh:213 # supervisor-gate
  - scripts/spawn-orchestrator.sh:223 # supervisor-scan
  - scripts/spawn-orchestrator.sh:148 # heartbeat
  - scripts/spawn-orchestrator.sh:67 # status-report
tags: [orchestrator, python, port, launchd, decision]
---

← [[orch_py_plan]]

## Context

This is the task the whole plan has been deferring, and it is a **decision task** as much as
a coding one.

> **Corrected 2026-07-13 after co-review of PR #205.** This card originally described a single
> constraint (launchd's PATH) over ~1,000 lines. Both were wrong: there are **two** constraints,
> and Tier B is **~2,000+ lines** — it also holds `doctor` (659), `status` (180),
> `classify_exit` (73), and `exit_reason` (69), which the plan had mis-assigned to Tier A.

Tier B is everything reachable from a context that cannot freely resolve a Python interpreter.
**Two distinct constraints**, and any option must satisfy *both*:

- **launchd's pinned minimal PATH** — the generated launch script's wake loop.
- **The Seatbelt `process-exec` allowlist** (`spawn-orchestrator.sh:711-724`) — the jailed
  run-phase agent invokes `doctor` (every loop iteration), `exit-reason` (every termination),
  `heartbeat`, and `alarm-clear` from *inside* `sandbox-exec`. An interpreter that is on the
  PATH but not on the exec allowlist **cannot run there at all**.

Plus the in-process trap: `status_report` calls `status` and `supervisor_check` calls
`classify_exit` as **bash functions**, not via the CLI — so those two cannot be ported and
deleted while their callers remain bash, regardless of interpreter.

The launchd-reachable set:

| Subcommand         | Lines | Role                                      |
| ------------------ | ----: | ----------------------------------------- |
| `status_report`    |   443 | periodic report, called by supervisor-scan |
| `supervisor_check` |   264 | post-invoke halt/relaunch decision         |
| `_supervisor_halt` |   150 | the halt path                              |
| `supervisor_scan`  |   114 | per-wake bookkeeping                       |
| `supervisor_gate`  |    60 | pre-invoke pause gate                      |
| `heartbeat`        |     — | liveness touch                             |

Together roughly 1,000+ lines. `write-launch` (task 4) deliberately kept the generated script
pointing at `spawn-orchestrator.sh` for all of these, so nothing about the wake loop's runtime
has changed yet.

Porting them means an **interpreter must be resolvable inside a launchd job with a minimal
PATH**, on every wake, forever. If it isn't, the supervisor loop breaks — and it breaks
*unattended, overnight*, which is the exact scenario auto-pilot exists for. That is why this
is last and why "don't" is a real answer.

The three options (plan open question 1):

- **(a) Freeze Tier B in bash** *(recommended default)*. `spawn-orchestrator.sh` ends at
  ~1,000–1,200 lines of supervisor loop — small enough to be tractable shell, and shell is a
  decent fit for a process-lifecycle loop. The 5,000+ lines of rendering, reporting, and
  diagnostics are already gone. **You have captured ~80% of the value at zero runtime risk.**
- **(b) Stdlib-only Python on absolute `/usr/bin/python3`**. Survives the minimal PATH (stable
  absolute path, always present on macOS), can be added to the seatbelt exec allowlist as a
  literal. Kills the bash entirely. Cost: `/usr/bin/python3` may be 3.9 on older macOS while
  the repo's other Python requires ≥3.11, and stdlib-only means no third-party deps.
- **(c) `uv` on the pinned PATH**. Cleanest code, largest new runtime dependency on the
  security-critical unattended path. Not recommended.

## Task

1. **Decide, with the user.** Present the real tradeoff: what breaks if the interpreter is
   missing at 3am, versus ~1,000 lines of bash that stay. Do not default silently.
2. **If (a)** — freeze it. Document the boundary in `dev_docs/orchestrator.md` (task 9): what
   is bash, what is Python, *why the line is drawn at launchd*, and the grep test from task 4
   that prevents the boundary being widened by accident. Harden the remaining bash: it is now
   the whole of the shell surface, so it should be fully ShellCheck-clean with no file-level
   disables. **This is a legitimate completion of the plan.**
3. **If (b) or (c)** — port `status-report`, `supervisor-scan`, `supervisor-gate`,
   `supervisor-check`, `heartbeat` (and `classify-exit`, if task 5 deferred it). Add the
   interpreter to the pinned `--path` and to the seatbelt exec allowlist. Then:
   - Add a **pre-flight guard**: `write-launch` must fail closed if the interpreter is not
     resolvable on the `--path` it was handed. A launch that would break on first wake must
     never be written.
   - Prove a real overnight run survives (see below). A green unit suite is **not** sufficient
     evidence for this change.

## Acceptance Criteria

**Code-enforced:**

- The decision is written down in the PR body with its rationale — not implied by the diff.
- If frozen: the remaining bash is ShellCheck-clean at `--severity=warning` with **no**
  file-level disables (the `SC2034` blanket disable must be gone — the arrays it excused
  belong to the ported code). The task-4 grep test still passes.
- If ported: `write-launch` fail-closes when the interpreter is absent from `--path`; a test
  asserts this. Golden corpus reproduces every ported subcommand byte-for-byte. Harness
  passes unchanged.
- `just check` green.

**User-run (mandatory if ported):**

- Run a **real, unattended, multi-wake auto-pilot run overnight** and confirm the supervisor
  woke, gated, checked, and reported on every cycle. Nothing short of this validates a change
  to the wake loop — the failure mode this task risks is silent, and only shows up when no
  one is watching.
