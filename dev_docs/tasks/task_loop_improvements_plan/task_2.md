---
title: Rank tasks by value/effort and surface assignee
priority: medium
size: 3
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
  - prioritization
expires: 2026-07-07
---

> Part of [[task_loop_improvements_plan]]. Depends on [[task_1]].

## Context

Today ordering is `priority` enum then age (`commands/process-tasks.md` step 1 "Sort by"; `commands/list-tasks.md` step 4 "sort by priority, then age"). With the new `impact` field (from the schema task) we can rank objectively by value/effort. `size` is effort; `impact` is value; a simple `impact / size` score gives a defensible default ordering. `priority` stays as the primary tiebreaker so humans can still force order.

`assignee` (also from the schema task) should be visible in the board.

`impact` uses the Fibonacci `1`/`2`/`3`/`5` scale (mirrors `size` and Linear's Fibonacci `estimate`). Note Linear has **no native value/impact field**, so this value/effort ranking is file-handler-only; the Linear handler continues to rank by `priority` + `estimate` (don't try to invent an impact field in Linear).

## Task

1. Define the ranking in `skills/task/SKILL.md` (a short "Ranking" subsection): primary sort `priority` (urgent>high>medium>low), then **value/effort score** `impact/size` descending (tasks missing `impact` rank last within their priority), then age (oldest first).
2. Update `commands/process-tasks.md` step 1 selection to apply this ranking when picking the highest-priority dependency-ready task.
3. Update `commands/list-tasks.md` step 4 sort to the same ranking, and render `assignee` inline on the card line when present (e.g. `— @dan`).
4. Keep behavior identical for tasks with no `impact` set (graceful default).

## Acceptance Criteria

- **Code-enforced:** `just check` passes.
- **User-run:** Given two `ready` tasks of equal `priority`, one `impact: 5 size: 2` and one `impact: 2 size: 5`, `/process-tasks` (no args) selects the first; `/list-tasks` lists it above the second. A task with `assignee: dan` shows `@dan` on its card.
- Tasks without `impact` still sort sensibly (by priority then age) and are not dropped.
