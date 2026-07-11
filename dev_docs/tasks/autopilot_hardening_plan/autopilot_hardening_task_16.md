---
title: "alarm: a halted or stalled run must actively tell a human — silence is the bug"
priority: urgent
size: 3
status: new
created: 2026-07-11
source_branch: main
related_files:
  - skills/auto-pilot/SKILL.md
  - skills/auto-pilot/references/run-budget.md
  - skills/auto-pilot/references/launch-runtime.md
  - scripts/spawn-orchestrator.sh
is_blocked_by: autopilot_hardening_task_10
parent: autopilot_hardening
tags: [auto-pilot, observability, alarm, p1]
---

[[autopilot_hardening_plan]]

## Context

The most expensive property of both production failures in detached run #2 was not
the bug — it was the **silence**.

- **#22:** an expired credential stalled the run at 01:37. The supervisor relaunched
  into the same non-retryable 401 for **4 hours and 14 minutes**. Nothing told
  anyone. It was caught only because a human happened to ask "how we doing?"
- **#23:** the orchestrator exited with the run's state gone from the working tree.
  It was caught only because a human had, minutes earlier, set up a 15-minute
  heartbeat check.

An unattended overnight run is *defined* by nobody watching it. So a run that can
fail silently has no working failure mode at all — the entire value of the circuit
breaker, the fatal-auth halt (task 10), and the invariant doctor (task 14) is
**conditional on somebody finding out**. Right now every one of them writes to
`REPORT.md`, a file on a branch nobody is reading at 3am.

The run's whole promise is "grind on this overnight and tell me what happened." It
currently keeps the first half.

**Corollary worth stating:** the alarm path must not depend on the agent. A
rate-limited or auth-dead orchestrator cannot make a model call to alert anyone —
so, like tasks 10/11, the alarm must be able to fire from the **supervisor, in
shell**.

## ALIGNMENT (2026-07-11) — the jail *forbids* the agent from alarming, so this isn't a preference

The substrate batch landed, and it turns the "fire from the supervisor" corollary
above from a **design preference into a hard constraint**. Check `main` before
implementing:

- **The jail denies `osascript`, `open`, and `launchctl` exec outright**
  (`scripts/orchestrator.sb.tmpl`: `(deny process-exec (literal "/usr/bin/osascript")
  (literal "/usr/bin/open") (literal "/bin/launchctl"))`), plus `mach-lookup` of the
  launchd/LaunchServices brokers. These denies are a **deliberate sandbox-escape
  fix** — do **not** relax them to make an alarm work. An alarm fired from **inside**
  the agent would be silently denied, which is the exact failure this task exists to
  prevent, wearing a disguise.
- **The supervisor wrapper is un-jailed.** `write_launch` wraps only `claude` in
  `sandbox-exec`; the wrapper shell that launchd runs is outside the jail. So
  `osascript` from the **supervisor** works, and is the only path that works.
- **The seam already exists.** Task 10 landed `spawn-orchestrator.sh
  supervisor-check` (+ `classify-exit`), which already halts and tears down on a
  fatal auth exit and on N consecutive no-progress wakes. **Extend that**; do not
  add a second wrapper. Its halt path already writes `status: systemic` and appends
  to `REPORT.md` — this task adds the *active* notification those writes currently lack.

## Task

- Define the **alarm conditions** (all terminal-ish, all currently silent):
  fatal auth halt (task 10, **landed** — hook the alarm onto its existing halt path) ·
  circuit-breaker `systemic` · a failed invariant
  (task 14) · N consecutive no-progress wakes (**landed** in `supervisor-check`) ·
  a park storm (≥N tasks parked) ·
  a run that blew its `--until` without finishing.
- On any alarm condition, **actively notify** — do not merely write a file:
  - **Primary (agent-independent):** the supervisor emits an OS-level notification
    (macOS `osascript -e 'display notification …'` / `terminal-notifier`) **and**
    writes a top-level `ALARM` marker (a sentinel file + a one-line reason at the
    very top of `REPORT.md`), so the alarm is visible from shell with no model call.
  - **Secondary (best-effort, when the agent is alive):** post a comment on the
    run's open PRs, or a `PushNotification`, naming the run, the condition, and the
    single next action for the human.
  - The notification must state **what a human has to do** ("re-authenticate:
    `claude /login`, then `/auto-pilot <source> --resume`"), not just that something
    broke. #22's fix was 20 seconds of human action gated behind 4 hours of silence.
- **Escalate on a stall, not only on a halt.** A run that is merely *not
  progressing* (task 14's invariant 7 / task 15's stale heartbeat) must alarm too —
  #22 never reached a halt state at all; it looked healthy and did nothing.
- Make the alarm **idempotent**: alarm once per condition per run, not once per
  300s wake, or the notification becomes the new noise.

## Acceptance Criteria

**Code-enforced:**
- A test per alarm condition: assert the `ALARM` sentinel + the `REPORT.md` top-line
  are written and the notification command is invoked (stub it and assert the call).
- A test asserts the alarm fires **from the supervisor with no model call** for the
  fatal-auth case (the agent is, by construction, dead).
- A test asserts idempotency: N repeated wakes in the same alarm condition produce
  **one** notification, not N.
- `bash scripts/check.sh` green (outside the jail).

**User-run:**
- Reproduce #22 (invalid credential): within one supervisor interval you get a
  desktop notification naming the run and saying to re-authenticate, `REPORT.md`
  leads with the alarm, and the job is torn down — instead of 4 hours of silence.
