---
title: Introduce /do-tasks (file/repo-pr execute path, WIP-bounded)
priority: high
size: 5
status: new
created: 2026-06-07
source_branch: claude/keen-tesla-pgLI4
related_files:
  - commands/process-tasks.md
  - commands/claim-task.md
  - skills/task/SKILL.md
  - commands/handlers/repo-pr.md
is_blocked_by:
  - wip-limit-batch
  - multi-blocker-deps
tags:
  - task-loop
  - do-tasks
  - structural
expires: 2026-07-07
---

> Part of [[task_loop_improvements_plan]]. Depends on [[task_5]] and [[task_3]].

## Context

Today execution is split awkwardly: `/process-tasks` (remote/batch/file-only) and `/claim-task` (foreground/single/tracker-only). The split is an artifact of build order, not workflow. We're unifying into one handler-dispatched verb, **`/do-tasks`**, that mirrors how `/add-task` / `/list-tasks` dispatch.

Agreed shape (from planning): single task by default; `--all` (or `-n N`) for batch; `--remote`/`--local` for where it runs. Batch is meaningful only for remote dispatch (foreground pairing is inherently single, so `--local` caps at 1). Batch is bounded by the WIP limit (from the WIP task). Multi-blocker readiness (from the multi-blocker task) governs which tasks are eligible.

**This task does the file/`repo-pr` path only** — it should fully subsume current `/process-tasks` behavior (remote dispatch prompt from `commands/process-tasks.md` step 4, `--local` mode, claim/execute/delete/PR). The tracker path and the alias/deprecation work are separate tasks. Do **not** delete `/process-tasks` or `/claim-task` yet.

## Task

1. Create `commands/do-tasks.md`: argument-hint `[slug | --all | -n N] [--remote|--local]`; resolve handler from `.task-config.yml`.
2. For `repo-pr`/absent handler: implement select → execute exactly as `/process-tasks` does today (scan `ready`, rank via value/effort + age, filter multi-blocker readiness, dispatch remote per step 4, or `--local` inline). Default = single highest-ranked; `--all`/`-n N` = batch bounded by `wip_limit`.
3. Carry the WIP cap and multi-blocker semantics through unchanged (reference, don't re-specify).
4. Add `/do-tasks` to the SKILL "Execute" section as the primary verb (the old commands are removed in a later task).
5. Leave `commands/process-tasks.md` in place and working for now — it is removed in task 12, not here. Don't delete `/claim-task` either.

## Acceptance Criteria

- **Code-enforced:** `just check` passes; `commands/do-tasks.md` exists with valid frontmatter; README component counts updated if a new command is counted.
- **User-run:** With `handler: repo-pr`, `/do-tasks` dispatches the single highest-ranked ready task; `/do-tasks --all` dispatches up to the WIP limit; `/do-tasks <slug> --local` processes that task inline. Behavior matches `/process-tasks` for the same inputs. `/process-tasks` still works (unchanged).
