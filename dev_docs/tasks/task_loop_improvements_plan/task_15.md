---
title: Add --claim-only and --no-claim flags to /do-tasks
priority: medium
size: 3
status: ready
created: 2026-06-07
source_branch: task/task_10
related_files:
  - commands/do-tasks.md
  - commands/handlers/linear-claim.md
  - skills/task/SKILL.md
is_blocked_by: task_11
tags:
  - task-loop
  - do-tasks
  - claim
expires: 2026-07-07
---

> Part of [[task_loop_improvements_plan]]. Depends on [[task_11]].

## Context

`/do-tasks` couples claiming and executing into one atomic step — correct for the headless default, but it hides a genuinely separable primitive. "Claim" (reserve/assign a task, move it to in-progress, without executing) is the kanban "pull into In Progress / assign to me" action and has standalone value in a board humans and agents share. The mirror — "execute a task I already claimed" — is needed for resume/retry.

Surfacing these as two flags makes claim and do composable: `--claim-only` now + `--no-claim` later equals one normal run, split across actors or time. This is the primitive that [[task_16]] builds size-gated auto-routing on top of.

Claim semantics are handler-aware, so this lands after the tracker path ([[task_11]]) exists, and must be defined for **both** the `repo-pr` and `linear` handlers.

## Task

1. `commands/do-tasks.md`: add `--claim-only` and `--no-claim` to the argument-hint and Modes, and document the three states:
   - **default** — atomic claim + do (unchanged).
   - **`--claim-only`** — perform the claim step and stop. No execution, no PR.
     - `repo-pr`: branch `task/<slug>`, flip `status: ready → in_progress`, commit, push. Do not delete the file or open a PR.
     - `linear`: follow `commands/handlers/linear-claim.md`'s claim sub-steps only (atomic `auto-claimed` guard, move to `started` state, record branch), then stop before execute.
   - **`--no-claim`** — skip the claim step and execute a task already claimed by the caller. Guard: only proceed when the task is already in `in_progress` (`repo-pr`) or assigned to the caller in a `started` state (`linear`); otherwise stop and explain, since executing unclaimed reopens the race the claim step closes.
2. Make the flags mutually exclusive — `--claim-only` with `--no-claim` is an error; stop and ask.
3. `--claim-only` is the one execute-family action that is safe to batch on trackers (no foreground execution), so `--all`/`-n N --claim-only` may reserve multiple tasks under the WIP gate; `--no-claim` is single (it resumes one already-claimed task).
4. Reference `linear-claim.md` for the tracker claim/execute detail rather than duplicating MCP calls.
5. Document both flags in the `/do-tasks` section of `skills/task/SKILL.md`.

## Acceptance Criteria

- **Code-enforced:** `just check` passes.
- **User-run:** `/do-tasks <slug> --claim-only` (repo-pr) branches and flips the file to `in_progress` without opening a PR; a later `/do-tasks <slug> --no-claim` executes it and opens the PR. `--claim-only` with `handler: linear` reserves the issue (auto-claimed, moved to started) without executing. `--no-claim` on an unclaimed task stops with an explanation. Passing both flags together errors.
