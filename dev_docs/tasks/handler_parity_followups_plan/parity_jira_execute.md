---
title: Decide jira single-execute support or record it as a non-goal
priority: low
size: 3
status: new
created: 2026-06-07
source_branch: bestdan/handler-parity-followups
related_files:
  - commands/task-config.md
  - commands/handlers/jira.md
  - commands/do-tasks.md
expires: 2026-09-07
tags:
  - task-loop
  - handlers
  - parity
---

> Part of [[handler_parity_followups_plan]].

## Context

Matrix cell: **jira × do / execute single** (`/do-tasks`).

The `/do-tasks` tracker execution path is Linear-only — claim one card, branch, execute, open a PR in the foreground. The jira handler has no execute path; the story is "pull the item manually." After the planned `/do-tasks` consolidation lands, jira execution remains unsupported.

This may be a **deliberate non-goal**: the project scoped tracker execution to Linear, and Jira execution may stay manual on purpose.

## Task

1. Decide whether `/do-tasks` (single, foreground) should support the jira handler.
2. If **build**: define what's involved — an unclaimed-item query, an atomic claim (assignee + workflow-state guard against parallel claims), a handler-published branch name, and a PR linked back so merge transitions the item to done. This mirrors `commands/handlers/linear-claim.md`; a `commands/handlers/jira-claim.md` would likely be the home.
3. If **non-goal**: mark the cell in the `commands/task-config.md` matrix as a deliberate non-goal and note in `commands/handlers/jira.md` that execution stays manual / Linear-only, with rationale.

## Acceptance Criteria

- The decision is recorded: either a jira single-execute path is implemented (matrix updated to supported), or the capability matrix in `commands/task-config.md` and `commands/handlers/jira.md` explicitly mark jira execute as a deliberate non-goal with rationale.
- `just check` passes.
