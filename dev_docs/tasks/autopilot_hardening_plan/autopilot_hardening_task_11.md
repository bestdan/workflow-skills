---
title: "supervisor: gate the relaunch on paused_until in shell — stop burning a model call every wake"
priority: 2
size: 2
status: new
created: 2026-07-11
source_branch: main
related_files:
  - scripts/spawn-orchestrator.sh
  - scripts/orchestrator.plist.tmpl
  - skills/auto-pilot/references/run-budget.md
  - skills/auto-pilot/references/launch-runtime.md
is_blocked_by: autopilot_hardening_task_10
parent: autopilot_hardening
tags: [auto-pilot, supervisor, budget, p2]
---

[[autopilot_hardening_plan]]

## Context

Finding **#19** — surfaced by detached run **#2**, during its rate-window pause.

The `launchd` job relaunches on a bare 300s `StartInterval`. During a
`paused_until` pause, that means **every 5 minutes** a full `claude -p`
orchestrator boots, loads the SKILL + references, reads `RUN.md`, applies the wake
guard, concludes "not yet time," and exits. The wake guard **works** — no work is
done before `paused_until`, so correctness is fine. The problem is purely cost: each
no-op wake spends a **model call** (prompt + context load), and it spends it
*during a rate-window pause* — precisely when tokens are scarcest and the run is
pausing **because** it has none to spare.

The check itself is a **pure timestamp comparison**. It needs no model, no context,
no reasoning. It belongs in the supervisor, in shell, **before** the agent is
invoked — not inside the agent that costs money to start.

This composes with task 10, which puts exit **classification** in that same
supervisor wrapper; both are instances of "the supervisor should decide in shell
what it can decide in shell." Blocked by task 10 because they edit the same wrapper.

## Task

- In the supervisor wrapper (emitted by `write_launch`), **before** invoking
  `claude -p`: read `paused_until` from the run-state branch's `RUN.md` front
  matter and, if `now < paused_until`, **exit 0 immediately** without starting the
  agent. Pure `date`/string comparison — no model call, no context load.
- Keep the agent-side wake guard as defense in depth (it is what a `--resume`
  relies on), but it should now be the *second* line, rarely reached.
- Read the run-level `status` too: a `done` / `systemic` run must **not** relaunch
  at all — the supervisor should tear itself down (this overlaps task 10's fatal
  path; reuse it rather than duplicating).
- Note in `run-budget.md` that the pause's cost is now **zero model calls**, which
  is what makes a long pause (a multi-hour window reset) actually cheap.

## Acceptance Criteria

**Code-enforced:**
- A test asserts: with `paused_until` in the future, the wrapper exits 0 and
  **never execs `claude`** (assert on the absence of the invocation, e.g. via a
  stubbed `--claude-bin` that records being called); with `paused_until` in the
  past or empty, it does exec it.
- A test asserts a `status: done` / `status: systemic` run-state causes the wrapper
  to tear down rather than relaunch.
- `bash scripts/check.sh` green (outside the jail).

**User-run:**
- Set `paused_until` an hour out, load the job, wait through two `StartInterval`
  wakes, and confirm the orchestrator log gains **no new model-call events** (and
  the usage window does not move) while the job still wakes and exits cleanly.
