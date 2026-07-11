---
title: "exit contract: distinguish 'paused mid-run' from 'run complete' — a heartbeat + an explicit exit reason"
priority: high
size: 3
status: new
created: 2026-07-11
source_branch: main
related_files:
  - skills/auto-pilot/SKILL.md
  - skills/auto-pilot/references/run-state.md
  - skills/auto-pilot/references/launch-runtime.md
  - scripts/spawn-orchestrator.sh
is_blocked_by: autopilot_hardening_task_8
parent: autopilot_hardening
tags: [auto-pilot, supervisor, observability, p2]
---

[[autopilot_hardening_plan]]

## Context

Observed in detached run **#2**: a single `claude -p` orchestrator does **not**
complete the whole run. It works until it runs out of turn/context, then exits
**cleanly** (`exit 0`, `terminal_reason: completed`) with tasks still pending. The
run continues only because the `launchd` `StartInterval` happens to relaunch it and
a fresh orchestrator picks up from the run-state branch.

That works — but it works **by accident**, and it makes the most important
distinction in the system invisible:

> **"I exited because I finished the run"** and
> **"I exited because I ran out of context mid-task"**
> are currently the *same observable event*: `exit 0`.

Everything downstream is guessing. The supervisor relaunches on a blind timer
because it cannot tell the two apart. A human reading `exit code = 0` cannot tell
whether the run is done or dead. This is the same "failure modes look like success"
class as #22/#23 — and it is why a genuinely finished run and a silently stalled one
are indistinguishable without reading `RUN.md` by hand.

Task 8 introduces a **done-sentinel**; this task makes it *load-bearing* by defining
the full exit vocabulary around it, and adds the **heartbeat** an external watcher
(a human, a cron, `status --label`) needs to tell "working" from "wedged."

## ALIGNMENT (2026-07-11) — the supervisor already classifies exits; this extends it

The substrate batch landed, and it built half of this task's machinery. Read `main`
first:

- **`spawn-orchestrator.sh classify-exit` / `supervisor-check`** (task 10) already
  classify an orchestrator exit — in shell, with no model call — into `done` /
  `fatal` / `retry`, and already relaunch vs tear down on that basis. This task's
  five-value vocabulary (`continuing` / `paused` / `done` / `systemic` / `deadline`)
  is a **refinement of that existing classifier**, not a new one. In particular
  `classify-exit` currently infers the bucket from the **exit code + log text**; this
  task's contribution is that the orchestrator should **declare** its reason
  explicitly, so the supervisor stops inferring what the agent already knows.
- **The done-sentinel exists** (`orchestrator.done`, written by `teardown
  --done-sentinel`, read by `status`) — task 8. The plan requires it be the **same**
  file as the launchd relaunch sentinel: honor that, extend it, do not add a second.
- **`status --label`** already reports the RUN.md status, phase table, PID liveness,
  `--until`, and the done-sentinel. The heartbeat and exit-reason are new **fields**
  on that existing surface.
- Task 10 also added a per-run `supervisor-state` file (consecutive-failure counter +
  last-seen run-state HEAD). Consider whether the heartbeat belongs there rather than
  in a new file — but note it is deliberately **not** committed to the run-state
  branch, while an exit reason must be.

## Task

- Define an explicit **exit reason**, written to the run-state branch *before* the
  orchestrator exits, in every termination path:
  - `continuing` — work remains, context exhausted. **Relaunch me.**
  - `paused` — rate window / `paused_until` set. **Relaunch me past the reset.**
  - `done` — no ready tasks remain. **Tear down; do not relaunch.**
  - `systemic` — circuit breaker / fatal auth / failed invariant. **Tear down; alarm.**
  - `deadline` — the pre-dispatch guard stopped with tasks ready. **Tear down; resume only by explicit `--resume`.**
- The **supervisor reads the exit reason in shell** (no model call) and acts on it —
  relaunch vs tear down — instead of relaunching on a blind timer. This is the same
  shell-side-decision seam as tasks 10 and 11; reuse that wrapper.
- Make the **done-sentinel from task 8 the single file** that encodes this (the plan
  already requires the done-sentinel and the launchd relaunch sentinel be the **same**
  file — honor that; do not add a second one).
- Add a **heartbeat**: the orchestrator touches a timestamp (on the run-state branch
  or the sentinel) at each loop iteration and each `/deliver-task` sub-step boundary.
  A watcher can then say "last heartbeat 40 minutes ago, per-task ceiling is 45m" —
  i.e. distinguish *slow* from *wedged*, which no current signal can do.
- Surface both in `status --label` (task 8): last heartbeat, exit reason, and whether
  a relaunch is expected.

## Acceptance Criteria

**Code-enforced:**
- A test asserts each exit reason is written on its termination path, and that the
  supervisor **relaunches** on `continuing`/`paused` and **tears down** on
  `done`/`systemic`/`deadline`.
- A test asserts a stale heartbeat (older than the per-task ceiling) is reported as
  a stall by `status`, while a fresh one is reported as healthy.
- `bash scripts/check.sh` green (outside the jail).

**User-run:**
- Kill an orchestrator mid-task: `status --label` reports a stale heartbeat and
  `continuing`, and the supervisor relaunches. Let a run finish: it writes `done`,
  the supervisor tears itself down, and `status` says so — no blind-timer relaunch
  of a finished run.
