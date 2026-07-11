---
title: "run loop: never check out a task branch in the run worktree — isolate task work, keep run state on HEAD"
priority: 2
size: 3
status: new
created: 2026-07-11
source_branch: main
related_files:
  - skills/auto-pilot/SKILL.md
  - skills/auto-pilot/references/run-state.md
  - commands/deliver-task.md
is_blocked_by:
parent: autopilot_hardening
tags: [auto-pilot, run-loop, resume, p2]
---

[[autopilot_hardening_plan]]

## Context

Finding **#23** — observed directly in detached run **#2**. While delivering
`task_3`, the run worktree's `HEAD` was found checked out on
`auto-pilot/hardening-task_3` — the **task branch** — rather than on the run-state
branch `auto-pilot/<run_id>`. The orchestrator was doing task work by switching
branches **inside the run worktree itself**, then switching back to commit run
state.

It *works* (task_2 and task_3 both shipped this way), which is exactly why it is
dangerous: it is a silent deviation from the design, and it breaks a load-bearing
assumption of crash recovery.

**Why it matters.** `--resume` and the run loop both assume the run worktree is the
**run-state branch checkout** — that is where `.auto-pilot/RUN.md`,
`QUESTIONS.md`, and `REPORT.md` live (`run-state.md` "The three files"). If the
orchestrator dies while `HEAD` is parked on a task branch — which is precisely when
a crash is most likely, since that is when the real work happens — then:

- `--resume` reads `.auto-pilot/RUN.md` from the **task branch's** working tree,
  where it is stale or absent entirely.
- The reconciliation table's premise ("re-read `RUN.md` from the run-state branch")
  is violated before a single row is matched.
- Uncommitted task-branch edits can block the checkout back to the run-state branch,
  wedging the recovery.

The design already names the right shape — `/deliver-task` workers run in **their
own worktrees** (`run-state.md` phases table: "Worker worktree: may exist"; the
`implementing`/`iterating` rows) — but nothing **enforces** it, so an orchestrator
that finds branch-switching simpler will just do that. This task makes the
invariant explicit and checkable.

## Task

- State the invariant plainly in `SKILL.md` ("Run phase") and `run-state.md`: the
  **run worktree's `HEAD` stays on the run-state branch for the entire run**. Task
  code is written in a **separate worker worktree** on the task branch; the
  orchestrator never runs `git checkout <task-branch>` in the run worktree.
- Make `/deliver-task` (`commands/deliver-task.md`) responsible for creating and
  removing its own worker worktree, and say so where it currently only implies it.
- Add a **guard**: at each run-loop iteration (and at the top of `--resume`), assert
  `git rev-parse --abbrev-ref HEAD` in the run worktree equals
  `auto-pilot/<run_id>`. If it does not, restore it before proceeding and record the
  deviation in `REPORT.md` — a run that finds itself on the wrong branch has already
  violated its recovery contract and must not silently continue.
- Have `--resume` **read run state from the branch, not the working tree**
  (`git show auto-pilot/<run_id>:.auto-pilot/RUN.md`) so a mis-parked `HEAD` cannot
  feed it a stale or missing `RUN.md` in the first place. This is the belt to the
  guard's braces, and it is what makes recovery robust even against an orchestrator
  that misbehaves.

## Acceptance Criteria

**Code-enforced:**
- A test asserts the HEAD guard fires: with the run worktree checked out on a task
  branch, the guard detects it, restores the run-state branch, and records the
  deviation (rather than proceeding).
- A test asserts `--resume` recovers `RUN.md` correctly **even when** the run
  worktree's `HEAD` is parked on a task branch (reads via `git show <branch>:<path>`,
  not the working tree).
- `bash scripts/check.sh` green (outside the jail).

**User-run:**
- Park a run worktree's `HEAD` on a task branch, then `/auto-pilot <source>
  --resume`: it reconciles from the correct `RUN.md`, restores `HEAD` to the
  run-state branch, and notes the deviation — instead of reading a stale/absent
  `RUN.md`.
