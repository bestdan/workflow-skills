---
title: Support multiple blockers in is_blocked_by across scan/list/process
priority: medium
size: 2
status: new
created: 2026-06-07
source_branch: claude/keen-tesla-pgLI4
related_files:
  - commands/process-tasks.md
  - commands/list-tasks.md
  - skills/task/SKILL.md
is_blocked_by: task-format-schema
tags:
  - task-loop
  - dependencies
expires: 2026-07-07
---

> Part of [[task_loop_improvements_plan]]. Depends on [[task_1]].

## Context

`is_blocked_by` is single-valued today; real dependency graphs need "blocked by A **and** B." The schema task makes the field accept a list. This task implements the *semantics*: a task is dependency-ready only when **every** referenced blocker is resolved (slug absent under `dev_docs/tasks/**/*.md`, or present with `status: done`).

Relevant logic lives in: `commands/process-tasks.md` step 1 (dependency readiness) and step 2; `commands/list-tasks.md` step 3 ("compute whether the task is currently dependency-blocked"); and the dependency description in `skills/task/SKILL.md`.

## Task

1. `commands/process-tasks.md`: update the dependency-readiness rule to iterate over all entries when `is_blocked_by` is a list — eligible only if ALL are satisfied; when reporting a blocked task, list every unresolved blocker.
2. `commands/list-tasks.md` step 3: same — "waiting on" annotation lists all unresolved blockers (e.g. `waiting on a, b`).
3. `skills/task/SKILL.md`: state the ALL-must-resolve rule wherever `is_blocked_by` semantics are described (scanning + kanban notes).
4. Preserve scalar back-compat: a single string behaves exactly as today.

## Acceptance Criteria

- **Code-enforced:** `just check` passes.
- **User-run:** A task with `is_blocked_by: [a, b]` where `a` is `done` but `b` still exists is reported by `/process-tasks` as waiting on `b` (not dispatched); once `b` is done too, it becomes eligible. `/list-tasks` shows `waiting on b`. A task with a single-string blocker behaves unchanged.
