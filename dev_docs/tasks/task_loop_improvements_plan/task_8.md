---
title: Add Linear promote path and dispatch /promote-tasks to the handler
priority: medium
size: 3
status: new
created: 2026-06-07
source_branch: claude/keen-tesla-pgLI4
related_files:
  - commands/promote-tasks.md
  - commands/handlers/linear-common.md
  - commands/handlers/linear-add.md
  - commands/handlers/linear-list.md
is_blocked_by: promote-scope-judgment
tags:
  - task-loop
  - handlers
  - linear
expires: 2026-07-07
---

> Part of [[task_loop_improvements_plan]]. Depends on [[task_7]].

## Context

`commands/handlers/linear-common.md` ("Kanban mapping" → Transitions) _describes_ a Linear promote behavior — "HIGH → move to `unstarted`/Todo + add `auto-eligible`; LOW → leave in backlog + add `human-approval-requested`" — but no code implements it: `/promote-tasks` (`commands/promote-tasks.md`) is file-only. This is doc↔impl drift. This task builds the missing path and makes `/promote-tasks` handler-aware, matching how `/add-task` and `/list-tasks` dispatch.

Depends on the promote-judgment task so the new scope gate is the one wired into both the file and Linear paths (avoids editing the scoring logic twice).

## Task

1. Create `commands/handlers/linear-promote.md`: preflight (shared from `linear-common.md`), fetch backlog-type issues, score each with the same confidence check (now judgment-based) used by the file path, then apply transitions per the kanban mapping (HIGH → `unstarted` state + `auto-eligible` label; LOW → add `human-approval-requested`). Never touch non-backlog issues.
2. `commands/promote-tasks.md`: add a step-0 handler resolution mirroring `/list-tasks` — `repo-pr`/absent → existing file path; `linear` → read and follow `linear-promote.md`; `gh-issue`/`jira` → stop with "promotion not supported for `<handler>`; promote in the tracker directly."
3. Keep the file path's behavior unchanged for `repo-pr`.

## Acceptance Criteria

- **Code-enforced:** `just check` passes; `linear-promote.md` exists and is referenced from `commands/promote-tasks.md`.
- **User-run:** With `handler: linear`, `/promote-tasks` moves a well-formed backlog issue to the Todo state and tags `auto-eligible`, and tags an under-specified one `human-approval-requested` without moving it. With `handler: repo-pr`, behavior is identical to before. With `handler: jira`, it stops with the unsupported message.
