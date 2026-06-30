---
title: Remaining consumers — /archive-tasks sweep + /push-plan targeting
priority: medium
size: 3
status: new
created: 2026-06-30
source_branch: main
related_files:
  - commands/handlers/linear-archive.md # sweep scope (~L117-119); sweep all configured projects
  - commands/push-plan.md # plan placement (~L115); prompt which project when multiple
is_blocked_by: [linear_multi_project_task_1]
parent: linear_multi_project
tags: [linear, archive-tasks, push-plan]
---

> Plan: [[linear_multi_project_plan]]

## Context

The two consumers the original spec missed (they landed on `main` after the spec was
written; caught in the 2026-06-30 refresh). Both read `default_project` today.

- `linear-archive.md` (~L117-119): `/archive-tasks` scopes its sweep by `default_project`
  (`project: { id: { eq: $projectId } }`) or sweeps the whole team when unset.
- `push-plan.md` (~L115): uses `default_project` to place a pushed plan's issues.

Design refs: spec §"Selection semantics" (`/archive-tasks`, `/push-plan` rows).

## Task

- `commands/handlers/linear-archive.md`: sweep across **all** configured projects — loop
  the archive query per `projectId` (union), instead of the single `default_project` eq.
  Whole-team scope (projects absent) unchanged. (`--project X` narrowing is **deferred** to
  the same follow-up as Task 3 — don't build it here.)
- `commands/push-plan.md`: when **multiple** projects are configured, **prompt** which one
  to push the plan into, instead of silently using the lone pin. With exactly one configured
  project, use it directly. (A plan goes to one project — see Open Questions on per-task
  routing. `--project X` narrowing is deferred with the rest of the flag.)

## Acceptance Criteria

- **Code-enforced:** `just check` passes. Neither file reads a scalar `default_project`
  for scoping (goes through the resolved list).
- **User-run:** with a 2-project config, confirm `/archive-tasks` sweeps both projects
  (trace the queries), and `/push-plan <name>` prompts which project to target. With a
  1-project config, confirm both behave as today (no extra prompt).
