---
title: Add tracker dispatch to /do-tasks (subsume /claim-task)
priority: high
size: 5
status: new
created: 2026-06-07
source_branch: claude/keen-tesla-pgLI4
related_files:
  - commands/do-tasks.md
  - commands/claim-task.md
  - commands/handlers/linear-claim.md
  - commands/handlers/linear-common.md
is_blocked_by: do-tasks-file-path
tags:
  - task-loop
  - do-tasks
  - linear
  - structural
expires: 2026-07-07
---

> Part of [[task_loop_improvements_plan]]. Depends on [[task_10]].

## Context

`/do-tasks` exists for the file path. This task adds the tracker path so `/do-tasks` fully subsumes `/claim-task` (`commands/claim-task.md`, Linear today via `commands/handlers/linear-claim.md`): find candidates → judge feasibility → claim → branch → execute → PR → move to review, with the bail path intact.

Map the unified flags onto tracker behavior: tracker execution is foreground/single by nature, so `--all`/batch is **not** supported for tracker handlers (`/do-tasks --all` with `handler: linear` should explain that and run a single claim). The feasibility judgment, atomic claim (concurrency guard), branch-name-verbatim rule, PR↔issue linking, and the "never move to completed" hard rule all carry over unchanged from `linear-claim.md`.

## Task

1. `commands/do-tasks.md`: extend handler resolution — `linear` → follow `commands/handlers/linear-claim.md` (feasibility, claim, branch, execute, PR, move-to-review, bail). `gh-issue`/`jira` → stop with "execution not supported for `<handler>`; pull an issue manually or switch to linear."
2. For `linear`, `--all`/`-n` degrades to a single claim with a one-line note (foreground/tracker is inherently single).
3. Preserve every safety property from `linear-claim.md`: atomic `auto-claimed` guard, verbatim `branchName`, `Closes <id>` + identifier-in-title linking, bail = stash + unclaim + human-approval, and the never-set-completed rule.
4. Reference `linear-claim.md` rather than duplicating its MCP-call detail.
5. Leave `commands/claim-task.md` working for now — it is removed in the next task (task 12), not here.

## Acceptance Criteria

- **Code-enforced:** `just check` passes.
- **User-run:** With `handler: linear`, `/do-tasks` (no args) claims one feasible unclaimed issue, branches with Linear's exact `branchName`, opens a PR with `[ID]` title + `Closes <ID>`, and moves the issue to In Review (never Done). A mid-execution bail stashes WIP and reverts the issue to backlog with `human-approval-requested`. `/do-tasks --all` with linear runs a single claim and says batch isn't supported for trackers. `/claim-task` still works unchanged here (it is removed in task 12).
