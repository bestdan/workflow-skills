---
title: "supervisor: classify a non-retryable auth failure (401) as fatal — halt, don't relaunch forever"
priority: 1
size: 3
status: new
created: 2026-07-11
source_branch: main
related_files:
  - scripts/spawn-orchestrator.sh
  - scripts/orchestrator.plist.tmpl
  - skills/auto-pilot/references/run-budget.md
  - skills/auto-pilot/references/launch-runtime.md
is_blocked_by: autopilot_hardening_task_1
parent: autopilot_hardening
tags: [auto-pilot, supervisor, budget, p0]
---

[[autopilot_hardening_plan]]

## Context

Finding **#22** — surfaced by detached run **#2**, and the single most expensive
bug that run hit. At 01:37 the launching user's Anthropic OAuth credential expired
mid-run. Every subsequent `claude -p` turn died on
`401 Invalid authentication credentials`. The `launchd` supervisor dutifully
relaunched on its 300s `StartInterval` — and **every one of ~52 wakes hit the same
401**. The run burned **4.3 hours** of wall-clock and window, did **zero** work, and
**raised no signal whatsoever**. It was only caught because a human happened to ask
"how we doing?".

Nothing in the current design catches this:

- The **run-level circuit breaker** (`run-budget.md`) counts *genuine delivery
  failures* — a crashed `/deliver-task`, a task parked on a failed verify. A
  process that dies on auth **before it can dispatch a task** never increments it.
- The **rate-limit backstop** (`launch-runtime.md` "Spawn mechanics") teaches the
  supervisor to classify exit code / stderr for a **rate-limit** signal (429 /
  `rate_limit_error` / overloaded) and reschedule. A `401` is a *different class*
  and is **not retryable by waiting** — the whole premise of the backstop
  (back off, the window resets) is false for an expired credential. Waiting
  changes nothing; only a human re-authenticating does.
- A **launch-time** credential probe cannot prevent it. Auth was live and
  smoke-tested at launch; it expired **hours later, mid-run**. The supervisor is
  therefore the *only* place that can catch this.

The general lesson, which the fix should encode: the supervisor already classifies
process exits into "retry later" — it needs a third bucket, **"stop, a human must
act"**, and anything unrecognized must fail toward *halting loudly* rather than
*relaunching silently*.

## Task

- In the supervisor wrapper (the launch script / plist relaunch path that
  `write_launch` emits — task 1 owns that generator, hence the block), classify the
  orchestrator's exit **with no model call**:
  - **retryable** → rate-limit signals (429 / `rate_limit_error` / overloaded):
    existing backoff + reschedule behavior, unchanged.
  - **fatal, non-retryable** → auth failures (`401`, `authentication_failed`,
    `Invalid authentication credentials`, `OAuth token has expired`): **halt the
    run**. Write run-level `status: systemic` + a `pause_reason` naming the auth
    failure to the run-state branch, **tear the supervisor down**
    (`launchctl bootout`), and surface one clear alarm entry in `REPORT.md`.
  - **unknown/unclassified repeated failure** → a **consecutive-no-progress
    guard**: if N consecutive supervisor wakes (default 3) exit non-zero with **no
    run-state commit between them** — i.e. the run made no forward progress — halt
    with `status: systemic` rather than relaunch indefinitely. This is the general
    backstop that would have caught #22 even without the specific 401 string match,
    and it must not fire during a legitimate `paused_until` wait (see task 11: a
    paused wake makes no progress *by design*).
- The classification is **shell-level**, before `claude -p` is invoked or on its
  exit — a rate-limited or auth-dead agent cannot run its own bookkeeping.
- Document the third bucket in `run-budget.md` (next to "Two pause kinds" — this is
  a **third** terminal kind: *neither* an agent pause *nor* a supervisor pause, but
  a **supervisor halt**) and in `launch-runtime.md` "Spawn mechanics".

## Acceptance Criteria

**Code-enforced:**
- A test drives the classifier over captured exit/stderr fixtures: a rate-limit
  signal → `retry`; a `401` / `authentication_failed` → `fatal`; a clean exit →
  `done`. A fatal classification is asserted to emit the `launchctl bootout`
  teardown and a `status: systemic` write.
- A test asserts the no-progress guard halts after N consecutive non-zero wakes
  with no intervening run-state commit, and does **not** halt across a legitimate
  `paused_until` pause.
- `bash scripts/check.sh` green (outside the jail).

**User-run:**
- Simulate the failure: point the launch script at an invalid credential, load the
  job, and confirm it halts **once** with a legible `REPORT.md` alarm and a torn-down
  `launchctl` job — instead of relaunching every 300s forever.
