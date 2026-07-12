---
title: "teardown says \"NOT booting the job out\" and then the retry boots it out anyway — gone-but-not-done"
priority: medium
size: 2
status: new
created: 2026-07-12
source_branch: main
related_files:
  - scripts/spawn-orchestrator.sh
  - scripts/test-spawn-orchestrator.sh
  - skills/auto-pilot/references/launch-runtime.md
is_blocked_by: [autopilot_hardening_task_26]
parent: autopilot_hardening
tags: [auto-pilot, teardown, sentinel, correctness, p2]
---

[[autopilot_hardening_plan]]

## Context

Found co-reviewing task 26 (#193), on the partial-failure case that is by far the most likely in practice: the **done-sentinel write fails but `launchctl bootout` works fine** (a read-only or full run dir on an otherwise healthy machine).

`teardown` is fail-closed by design. When `_write_done_sentinel` fails it `die`s with:

> FAILED to write the done-sentinel … — **NOT booting the job out** (a torn-down job with no completion marker is exactly the 'gone but not done' state the sentinel exists to prevent)

Task 26 subshelled that call so the halt survives the `die` — correct, and the halt now reaches `_verify_bootout`. But `_verify_bootout`'s retry (`spawn-orchestrator.sh:1215`) calls `teardown --label "$label"` **without** `--done-sentinel`. That path skips the sentinel entirely and **boots the job out anyway** — two lines after the log asserted it would not.

Net result on that host: job gone, **no done-sentinel**, and **no warning** (the retry succeeds, so the STILL-LOADED branch never fires). The sentinel is *also* the launchd relaunch sentinel and is what `status` reads, so the run now reads as **never finished, with no job left to finish it**. That is precisely the "gone but not done" state the `die` was written to prevent — reached through the code that handles the `die`.

This is **not** a regression from #193 — before it, the process simply died with the job still loaded, which is worse, and the halt does now fire its alarm, so a human is told *something*. But teardown's documented invariant (sentinel first, bootout second, never one without the other) is now violated on the path that matters, and the log contradicts itself, which will cost somebody an hour.

Note the tests cannot see this: the task-26 fixtures use a `launchctl` stub whose **bootout also fails**, so they only ever exercise the both-broken case, where STILL-LOADED happens to fire.

## Task

Decide what the contract actually is, then make the code and the log agree:

- Either the retry must **also** be sentinel-aware (pass `--done-sentinel`, and refuse to boot out without one) — preserving the invariant, at the cost of leaving the job loaded and relying on the STILL-LOADED warning + alarm;
- **or** booting out without a sentinel is deliberately acceptable as a last resort, in which case `teardown`'s `die` message must stop claiming otherwise, and the gone-but-not-done state must be **alarmed** rather than silent.

Do not leave it as-is: one of the two messages is a lie.

## Acceptance Criteria

- A test with a `launchctl` stub whose `bootout` **succeeds** while the run dir is unwritable — the case the current fixtures cannot reach — pins the chosen behavior.
- No path can end with the job booted out, no done-sentinel, and no alarm/warning.
- `teardown`'s fail-closed message is true of every path that can follow it.
- `bash scripts/check.sh` green.
