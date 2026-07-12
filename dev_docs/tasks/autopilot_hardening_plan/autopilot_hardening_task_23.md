---
title: "no-progress guard: the pause exemption has no independent authority"
priority: high
size: 3
status: new
created: 2026-07-12
source_branch: main
related_files:
  - scripts/spawn-orchestrator.sh
  - skills/auto-pilot/references/run-state.md
  - skills/auto-pilot/references/run-budget.md
is_blocked_by: [autopilot_hardening_task_15]
parent: autopilot_hardening
tags: [auto-pilot, supervisor, circuit-breaker, p1]
---

[[autopilot_hardening_plan]]

## Context

Found co-reviewing task 15 (#190). The no-progress guard exists to stop a run relaunching forever without advancing. It is exempted when the run is legitimately **paused** (a rate-window wait must be allowed to sit still).

Task 15 tightened the exemption: a declared `paused` exit reason exempts the guard **only if** `RUN.md` independently corroborates with `status: paused`. That conjunction is correctly implemented — but it **buys nothing**, because a pre-existing carve-out already on `main` exempts **any** `status: paused` wake regardless of declaration. The effective exemption is still `status: paused` **alone**.

And both halves are read from the **same agent-written `RUN.md`**. That is corroboration against the agent's own pause procedure, not an independent authority. **A buggy or wedged agent that writes `status: paused` on every wake — with no parseable `paused_until` — relaunches forever, resetting the counter each time.** The guard's whole purpose is defeated by the failure it is most likely to face.

Not a regression (task 15 made nothing worse), but the design point it advertises — "the guard's original purpose survives the fix" — overstates what the code does.

## Task

Give the exemption an authority the agent cannot forge:

- A `status: paused` exemption must be backed by a **parseable, future `paused_until`**. A pause with no bound is not a pause; it is a wedge. An unbounded/garbage `paused_until` must **not** exempt.
- Bound the total exempted time: a run may not sit `paused` indefinitely across unbounded wakes. Past a ceiling (or past `paused_until` by a margin), the guard must re-arm and eventually halt + alarm (task 16).
- Consider corroborating against something the agent does not write — e.g. the usage/rate-window signal the pause claims to be waiting on — rather than a second field in the same file.
- Collapse the now-redundant pre-existing carve-out so there is **one** exemption rule, not two that disagree.

## Acceptance Criteria

- An agent writing `status: paused` every wake with **no** `paused_until` **trips** the guard and halts (driven across N wakes against the real generated wrapper).
- A genuine rate-window pause (`status: paused` + parseable future `paused_until`) still waits and relaunches past the window reset — the task-11 behavior must not regress.
- A pause that overruns its own `paused_until` by more than the margin re-arms the guard.
- Mutation check: removing the `paused_until` requirement makes the first test fail.
- `bash scripts/check.sh` green.
