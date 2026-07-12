---
title: "run doctor: assert the run's own invariants every iteration — repair or halt, never drift"
priority: urgent
size: 3
status: in_progress
created: 2026-07-11
source_branch: main
related_files:
  - skills/auto-pilot/SKILL.md
  - skills/auto-pilot/references/run-state.md
  - scripts/spawn-orchestrator.sh
is_blocked_by: autopilot_hardening_task_13
parent: autopilot_hardening
tags: [auto-pilot, run-loop, resume, invariants, p1]
---

[[autopilot_hardening_plan]]

## Context

Generalizes findings **#22** and **#23**, both of which hit detached run #2 in
production and **both of which presented as a clean `exit 0`**.

That is the pattern worth fixing, not just the two instances: **auto-pilot's failure
modes look like success.** The 401 loop exited 0 fifty-two times. The vanished-run-
state exit was `exit 0`, `terminal_reason: completed`, `is_error: false`. Neither
tripped the circuit breaker, because the circuit breaker counts *delivery* failures
and neither of these was one. A run can be thoroughly broken while every signal the
system currently checks reads green.

Tasks 10 and 13 fix the two known instances. This task fixes the **class**: the run
never checks that its own world still makes sense. There is no cheap, per-iteration
audit that says "am I still a valid run?" — so drift is only ever caught by a human
noticing, which is exactly what happened (twice).

The check is cheap, deterministic, and needs no model reasoning. It should be
impossible for the loop to advance while an invariant is violated.

## ALIGNMENT (2026-07-11) — two invariants are already implemented; compose, don't reimplement

The substrate batch landed. Before writing anything, read what exists on `main`:

- **Invariant 1 (HEAD on the run-state branch) is already built** — task 13 shipped
  `spawn-orchestrator.sh assert-run-head --dir <run-worktree> --run-id <run_id>
  [--questions <path>]`, which asserts, restores, and records the deviation. The
  doctor must **call it**, not re-derive it.
- **Invariant 2's "read from the branch" is already the rule** — `--resume` now reads
  `RUN.md` via `git show auto-pilot/<run_id>:.auto-pilot/RUN.md`
  (`references/resume.md`). Reuse that read; a second, working-tree-based reader would
  reintroduce finding #23.
- **Invariant 7 (forward progress) already has a supervisor-side implementation** —
  task 10 shipped a consecutive-no-progress guard in `supervisor-check` (keyed on the
  run-state HEAD not moving across wakes, skipped during a legitimate pause). This
  task's agent-side guard must **complement, not duplicate** it: the two must not
  both halt the same run for the same reason, and the doctor must not fire while the
  run is legitimately paused. State which one owns the halt.

The remaining invariants (3, 4, 5, 6) are genuinely unbuilt.

## Task

Add a **run doctor** — an invariant audit run at the **top of every run-loop
iteration** and at the top of `--resume`. Each invariant has a stated repair or a
halt; none may be silently ignored.

| # | Invariant | On violation |
| - | --------- | ------------ |
| 1 | The run worktree's `HEAD` is the run-state branch | **repair** — check it out, record the deviation (task 13) |
| 2 | `RUN.md` / `QUESTIONS.md` / `REPORT.md` are readable **from the branch** (`git show <branch>:<path>`) and `RUN.md`'s front matter parses | **halt** `systemic` — the run has no memory; do not guess |
| 3 | Every task at `pr-open` / `in-review` / `iterating` / `handed-off` has a **PR that actually exists and is open** | **repair** — reconcile the phase from git+PR reality (the G-table) |
| 4 | Every task at `handed-off` has its task-file `status: needs_review` | **repair** — write the missing tracker/status flip (a G6/G7 crash gap) |
| 5 | No orphan worker worktrees from a dead dispatch | **repair** — remove them (G2) |
| 6 | A chained task's parent tip still equals its frozen `base_sha` | **park** the child (existing stacked-PR rule) |
| 7 | The run made **forward progress** since the last iteration (a new run-state commit, or a phase advance) | after N consecutive no-progress iterations → **halt** `systemic` (complements task 10's supervisor-side guard, on the agent side) |

- Emit the audit result as a one-line summary to the log each iteration, so a stall
  is visible in the log without parsing stream-json.
- A **repair** is recorded in `REPORT.md` (a run that had to repair itself is a
  signal, even when it recovers). A **halt** writes `status: systemic` + the failing
  invariant and tears the supervisor down.
- Reuse this same audit as the body of `--resume`'s reconciliation pass rather than
  writing a second, divergent implementation — resume *is* the doctor, run once.

## Acceptance Criteria

**Code-enforced:**
- A test per invariant: construct the violated state, assert the doctor detects it
  and applies the stated repair/halt. Invariant 1 (HEAD on a task branch) and
  invariant 2 (run state unreadable) must be covered explicitly — those are the two
  that shipped as production failures.
- A test asserts the loop **cannot advance** to a `/deliver-task` dispatch while an
  invariant is violated.
- `bash scripts/check.sh` green (outside the jail).

**User-run:**
- Manually park the run worktree's `HEAD` on a task branch and delete `RUN.md` from
  the working tree, then wake the orchestrator: it detects both, repairs HEAD,
  recovers `RUN.md` from the branch, records the deviation in `REPORT.md`, and
  continues — instead of exiting 0 into a stateless void.
