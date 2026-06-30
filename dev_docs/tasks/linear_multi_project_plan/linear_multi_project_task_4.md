---
title: Selection prompts — /add-task, /list-tasks, /promote-tasks scoping
priority: medium
size: 3
status: new
created: 2026-06-30
source_branch: main
related_files:
  - commands/handlers/linear-add.md # prompt among configured projects (2 default_project refs)
  - commands/handlers/linear-list.md # pick-one / `all` union (2 refs)
  - commands/handlers/linear-promote.md # scope among configured; `all` = all configured (1 ref)
is_blocked_by: [linear_multi_project_task_1]
parent: linear_multi_project
tags: [linear, add-task, list-tasks, promote-tasks]
---

> Plan: [[linear_multi_project_plan]]

## Context

The three "selection" consumers that prompt for / scope to a project. Each does the same
small change: read Task 1's resolved projects list instead of the scalar `default_project`,
and prompt/scope among them. Same pattern across three files → one PR.

Design refs: spec §"Selection semantics per command" (`/add-task`, `/list-tasks`,
`/promote-tasks` rows).

## Task

- `commands/handlers/linear-add.md`: prompt among the **configured** projects only —
  **no** "team backlog" option once `projects` are configured (resolved planning decision:
  every task must go to a configured project). **Skip the prompt** when exactly one project
  is configured (use it directly). When `projects` is absent/empty, behavior is unchanged
  (whole-team / today's flow).
- `commands/handlers/linear-list.md`: prompt to pick one configured project; `/list-tasks
  all` shows the **union**, grouped/labeled by project.
- `commands/handlers/linear-promote.md`: prompt to pick one configured project;
  `/promote-tasks all` scores **all configured** backlogs (not the whole team).

In all three, "all" means **all configured projects**, not the whole team backlog.

## Acceptance Criteria

- **Code-enforced:** `just check` passes. None of the three files reads a scalar
  `default_project` for scoping anymore (`rg default_project` on them is empty or
  migration-comment only).
- **User-run:** with a 2-project config, confirm `/add-task` prompts the two configured
  projects only (no team-backlog option); with a 1-project config, confirm it skips the
  prompt. Confirm `/list-tasks all` groups by project and `/promote-tasks all` scores both
  configured backlogs.
