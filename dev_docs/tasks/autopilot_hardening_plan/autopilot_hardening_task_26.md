---
title: "sweep: `die` is `exit` — audit every `|| true` wrapped around a function that can die"
priority: high
size: 2
status: new
created: 2026-07-12
source_branch: main
related_files:
  - scripts/spawn-orchestrator.sh
  - scripts/test-spawn-orchestrator.sh
parent: autopilot_hardening
tags: [auto-pilot, shell, fail-safe, sweep, p1]
---

[[autopilot_hardening_plan]]

## Context

Found co-reviewing task 16 (#191). `die` is `exit 2`, and **an `exit` inside a same-shell function terminates the process regardless of `|| true`**. So `some_fn ... || true`, written to mean "best effort, never fatal," is **not** best-effort if `some_fn` can `die`.

Two live instances were found by accident:

- `_supervisor_halt` called `alarm ... || true` with a comment promising the halt would proceed regardless. An unwritable ALARM sentinel made `alarm` `die` — the process exited **2** before the run-state commit **and** before `teardown`. Reproduced: zero `launchctl` calls. The job stayed loaded, and the next wake hit the (already `systemic`) gate and tore down silently. Total silence, from a "best effort" notification.
- Task 14's doctor hit the same shape: `_set_front_field`/`_upsert_front_field` `die` on absent front matter, and invariant 2 halts **precisely when front matter does not parse** — the halt would have exited before its own alarm and teardown.

Both were found by reading, not by a test. There are likely more. This is a **grep**, and the class is high-severity: it converts a recoverable error into a silent, terminal one on exactly the paths that exist to handle errors.

## Task

- Enumerate every function that can `die` (directly or transitively).
- Enumerate every call site that wraps one in `|| true`, `|| warn`, `if ! fn`, or otherwise assumes non-fatality — especially inside halt/teardown/cleanup paths, where a spurious `exit` strands the very recovery the code exists to perform.
- Fix each: subshell the call (`( fn ... ) || warn`), or convert the function's post-validation failures to `return` and reserve `die` for argument validation (the pattern #191 adopted with `_alarm_safe`).
- Prefer making it **structurally impossible**: a documented convention that any function called from a halt/cleanup path returns rather than exits, plus a test that a broken side channel cannot prevent a teardown.

## Acceptance Criteria

- Every `die`-capable function called in a "best effort" position is either subshelled or converted to `return`.
- A test per halt/cleanup path: with the side channel **broken** (unwritable sentinel, absent front matter, failing notifier), the halt still commits run-state and still tears down.
- Mutation check: restoring a `die` on any of these paths turns the corresponding test red.
- `bash scripts/check.sh` green.
