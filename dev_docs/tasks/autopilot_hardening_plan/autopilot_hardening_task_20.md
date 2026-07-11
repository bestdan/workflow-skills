---
title: "periodic status report — ship the heartbeat the operator had to hand-roll; catches the failures no alarm predicted"
priority: 1
size: 3
status: new
created: 2026-07-11
source_branch: main
related_files:
  - scripts/spawn-orchestrator.sh
  - skills/auto-pilot/SKILL.md
  - skills/auto-pilot/references/launch-runtime.md
  - skills/auto-pilot/references/run-state.md
is_blocked_by: autopilot_hardening_task_15
parent: autopilot_hardening
tags: [auto-pilot, observability, status, p1]
---

[[autopilot_hardening_plan]]

## Context

Finding **#28** — from the human, unprompted, after detached run #2: *"no matter what,
the 'status report every 15 minutes' has been very important."*

It was. During run #2 the operator had to **hand-roll** it — a `/loop 15m` cron firing a
bespoke shell pipeline that scraped `launchctl print`, `git show <branch>:RUN.md`, task-
branch commit timestamps, `claude-usage.sh`, and `gh pr list`, then diffed it all against
the previous check. That improvised loop is what actually **found the bugs**:

- **#23** (orchestrator exited with `HEAD` parked on a task branch, destroying its own run
  state) was caught **within 7 minutes** — by a routine check noticing reality did not
  match `RUN.md`. **Nothing alarmed. Nothing could have** — that failure mode was unknown
  at the time.
- **#22** (the 401 relaunch loop) ran **4h14m in silence** precisely because no such check
  existed yet.
- The "is it working or wedged?" question could only be answered by hand-diffing task-
  branch commit timestamps, because `RUN.md`'s phase table legitimately lags reality.

**Why this is NOT task 16 (the alarm channel).** An alarm fires on a **predicted**
condition. The periodic report is **unconditional**, and that is the entire point: it
surfaces the failures **nobody anticipated**. #23 is the proof — no alarm rule would have
existed for it, because we did not know it was possible until the report exposed it.
Alarms cover the known; the heartbeat covers the unknown. Ship both.

**Why a *positive* report, not just exception-reporting.** Run #2 established that **every
failure mode looks like success** (`exit 0`, `is_error: false`, `terminal_reason:
completed`). In such a system **silence is indistinguishable from health**, so
"no news is good news" is actively false. A periodic *affirmative* signal — "still
working, task_7, 12 min elapsed, 3 PRs open, window 38%" — is the only thing that makes
the *absence* of a report meaningful.

**Why it belongs in the tool, not the operator's head.** The operator had to know to ask
for it, and had to build it from five different commands. That is a control that exists
only when someone thinks of it — the same "rule with no enforcement" failure this plan
keeps finding. It should be **on by default**.

This directly serves the **partially attended** mode named in task 19 — a human checking
in periodically, which run #2 showed is the *common* case, not the exception.

## Task

- Add a **periodic status report** to the run, **on by default**, interval configurable
  (`--report-every <dur>`, default **15m**; `off` to disable).
- **Emit it from the supervisor, in shell — no model call.** Same through-line as tasks
  10/11/16: an agent that is wedged, rate-limited, or auth-dead cannot report on itself,
  and the report is most valuable exactly when the agent is not healthy. Build it on task
  8's `status --label` and task 15's heartbeat/exit-reason (hence the block).
- **Content** — everything the hand-rolled loop had to assemble, in one place:
  - phase table (handed-off / in-flight / pending) + the **in-flight task's elapsed time
    vs the per-task ceiling** (the working-vs-wedged signal);
  - **last heartbeat age** and current exit-reason/`status`;
  - open PRs + their **mergeable** state;
  - rate-window percent + reset;
  - **`--until` remaining** vs `min_task_budget`.
- **The delta is the payload.** Report **what changed since the last report**, and state
  plainly when **nothing** did — *"no forward progress in 15m; task_7 in-flight 31m of
  45m ceiling."* No-forward-progress is the single highest-value line: it is what
  distinguishes a slow task from a wedged run, and it is what a human actually scans for.
- **Reconcile against reality, don't just echo `RUN.md`.** `RUN.md` is allowed to lag
  (remote ≥ tracker ≥ run files). The report must cross-check the branch/PR state so a
  stale phase table cannot read as healthy — **this cross-check is exactly what caught
  #23**, and it is the one piece a naive `cat RUN.md` would miss.
- **Delivery** — write to a predictable path (`.auto-pilot/STATUS.md`, overwritten each
  interval) **and** emit a one-line digest to the log; make it trivially pollable by a
  human, a `/loop`, or a terminal notification. Route genuine alarm conditions through
  task 16 rather than duplicating them here.

## Acceptance Criteria

**Code-enforced:**
- A test asserts the report renders phase table, in-flight elapsed, heartbeat age, PR
  states, rate window, and `--until` remaining from a fixture run dir.
- A test asserts the **no-forward-progress** line fires when nothing changed between two
  reports, and does **not** fire when a phase advanced.
- A test asserts the report is produced with **no model call** (stub `--claude-bin` and
  assert it is never invoked).
- A test asserts a **stale `RUN.md` cannot read as healthy**: with `RUN.md` claiming
  `claimed` while the branch has an open PR, the report flags the divergence (the #23
  scenario).
- `bash scripts/check.sh` green (outside the jail).

**User-run:**
- Launch a run and walk away: every 15 minutes `.auto-pilot/STATUS.md` shows what changed,
  what is in flight and for how long, and says so explicitly when nothing moved — with no
  `/loop`, no cron, and no hand-written shell pipeline.
