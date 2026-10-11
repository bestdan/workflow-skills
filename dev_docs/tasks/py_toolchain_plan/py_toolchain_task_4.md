---
title: Remove the baseline — enforce strict repo-wide with no suppressions
priority: medium
size: 2
status: ready
created: 2026-07-13
expires: 2026-12-31
source_branch: bestdan/port-orchestrator-to-python
parent: py_toolchain
is_blocked_by: py_toolchain_task_3
related_files:
  - pyproject.toml
  - scripts/check.sh
tags: [python, toolchain, cleanup]
---

← [[py_toolchain_plan]]

## Context

Task 1 landed the gate green by suppressing existing violations per-file. Tasks 2 and 3 removed
those suppressions one group at a time. If they did their jobs, **no suppressions remain** —
and the baseline mechanism is now scaffolding with nothing to hold up.

Scaffolding that outlives its purpose becomes permanent by default. A per-file ignore list that
still exists is an invitation to add to it, and the next person to hit a type error will reach
for the suppression instead of the fix. This task removes the temptation.

## Task

- Confirm every per-file suppression added in task 1 is gone.
- **Delete the suppression mechanism itself** from `pyproject.toml` — not just its entries. If
  the config still has an empty per-file-ignores block, it will be filled again.
- Verify the strictest intended settings are actually active (it is easy for a strictness knob
  to have been quietly relaxed during tasks 2–3 to make a file pass; check the config diff
  against task 1's intent).
- Re-confirm the two guard tests from task 1 still pass: the checker rejects a deliberately
  broken file, and rejects an unhandled `Literal` variant under `match` + `assert_never`.

## Acceptance Criteria

**Code-enforced:**

- `rg` for the suppression directive across the repo returns **nothing**.
- The suppression mechanism is absent from `pyproject.toml`, not merely empty.
- `pyrefly` runs at the strictness task 1 specified — confirmed by reading the config, not by
  it being green (a relaxed config is also green).
- The "checker actually fails on broken code" and "exhaustiveness is enforced" guard tests pass.
- `just check` green; `just lint-py` under its time budget.
- All 1,845 lines of existing Python are strict-clean with no escapes.
