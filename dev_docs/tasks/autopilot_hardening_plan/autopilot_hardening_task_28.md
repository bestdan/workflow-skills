---
title: "supervisor-gate tears down without verifying the bootout — finding #22 by a third route"
priority: high
size: 1
status: new
created: 2026-07-12
source_branch: main
related_files:
  - scripts/spawn-orchestrator.sh
  - scripts/test-spawn-orchestrator.sh
is_blocked_by: [autopilot_hardening_task_26]
parent: autopilot_hardening
tags: [auto-pilot, supervisor, fail-loud, finding-22, p1]
---

[[autopilot_hardening_plan]]

## Context

Found co-reviewing task 26 (#193). Finding #22 is the relaunch loop that runs forever with **zero work and zero alarm** because a failed `launchctl bootout` leaves the job loaded. `_verify_bootout` exists to turn exactly that into a LOUD warning, and it is now called on **two** of the three supervisor teardown paths:

- `_supervisor_halt` — verifies.
- `supervisor_check`'s `done|deadline` branch — verifies (its comment even says why: *"an unverified failure here leaves the job loaded — StartInterval then wakes a FINISHED run forever… Zero work, zero alarm: finding #22 by another route"*).
- **`supervisor_gate` (`spawn-orchestrator.sh:2464`) — does not.** On a `done`/`systemic` run it calls a bare `teardown --label "$label"` and returns 20. If the bootout fails, the job stays loaded, the next wake re-enters the gate, tears down again, exits 0 — silently, forever.

It self-heals *if* the bootout ever starts working, so this is less severe than the halt-path variants. But it is the same defect the other two call sites were fixed for, and it is **silent** in the meantime: the operator is never told the job is stuck.

The tests do not catch it because they assert the **message**, not the effect: `have "gate: status done reports a teardown" "torn down"` passes whether or not `launchctl bootout` did anything. That is the "a test exists but does not guard" pattern from `auto-pilot-developer-review-feedback.md`.

## Task

- Call `_verify_bootout "$label" || true` after `supervisor_gate`'s teardown, matching the other two paths.
- Decide, explicitly, whether a still-loaded job at the gate should also **alarm** (the halt path does). It is a finished run whose job will not die — arguably exactly what the alarm channel is for. If it should not alarm, say why in a comment.
- Replace the message-only gate assertions with ones that assert against the **stubbed `launchctl` call log**, not the stdout string.

## Acceptance Criteria

- With a `launchctl` stub whose `bootout` fails and whose `print` reports the job still loaded, the gate path emits the STILL-LOADED warning (and alarms, if that is the call made above).
- The existing gate tests assert the bootout was actually attempted, not merely announced.
- Mutation check: removing the `_verify_bootout` call turns a named test red.
- `bash scripts/check.sh` green.
