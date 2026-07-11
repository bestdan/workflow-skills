---
title: "cleanup: graduate hardening design to dev_docs/ + delete plan scaffolding"
priority: 4
size: 1
status: new
created: 2026-07-10
source_branch: main
related_files:
  - dev_docs/auto-pilot-hardening.md
  - dev_docs/autopilot-detached-run-1-findings.md
  - dev_docs/tasks/autopilot_hardening_plan/
is_blocked_by:
  - autopilot_hardening_task_1
  - autopilot_hardening_task_2
  - autopilot_hardening_task_3
  - autopilot_hardening_task_4
  - autopilot_hardening_task_5
  - autopilot_hardening_task_6
  - autopilot_hardening_task_7
  - autopilot_hardening_task_8
parent: autopilot_hardening
tags: [auto-pilot, cleanup, docs]
---

[[autopilot_hardening_plan]]

## Context

Per the plan-with-docs lifecycle: once the work lands, graduate the durable design
into a permanent doc and remove the temporary plan scaffolding. Blocked by every
other task so it runs last.

## Task

- Write `dev_docs/auto-pilot-hardening.md` capturing the durable outcomes: the
  final spawn-orchestrator contract (verbose + PATH + toolchain-exec + write-scope
  split), the **verify-outside-the-jail** posture and why (the jail defeats the
  project's own harness), the pre-flight helper's inputs/outputs, and the plan-
  adapter clarifications (no reservation PR, scaffolding-as-teardown, merge-order).
  Cross-reference `dev_docs/autopilot-detached-run-1-findings.md` (the source
  findings) and the earlier `dev_docs/autopilot-dry-run.md`.
- Delete the `dev_docs/tasks/autopilot_hardening_plan/` folder (and any notes it
  spawned) **following task 6's run-level-teardown guidance** — do not attempt it as
  a graph task from a `main`-based code PR. That is exactly finding #13's
  impossibility (the folder lives only on the working/run-state branch, never on
  `main`), which task 6 documents; task 9 must *follow* that guidance, not re-trip
  over it. Do the deletion on the branch where the scaffolding actually lives (or
  manually at plan completion).

## Acceptance Criteria

**Code-enforced:**
- `bash scripts/check.sh` green after the doc lands and the folder is removed.

**User-run:**
- `dev_docs/auto-pilot-hardening.md` reads as a standalone design a new dev could
  follow; `dev_docs/tasks/autopilot_hardening_plan/` is gone (or confirmed removed
  on the appropriate branch).
