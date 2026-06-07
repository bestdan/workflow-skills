---
title: Decide gh-issue single-execute support or record it as a non-goal
priority: low
size: 3
status: new
created: 2026-06-07
source_branch: bestdan/handler-parity-followups
related_files:
  - commands/task-config.md
  - commands/handlers/gh-issue.md
  - commands/do-tasks.md
expires: 2026-09-07
tags:
  - task-loop
  - handlers
  - parity
---

> Part of [[handler_parity_followups_plan]].

## Context

Matrix cell: **gh-issue × do / execute single** (`/do-tasks`).

The `/do-tasks` tracker execution path is Linear-only — it claims one tracker-side card, branches, executes, and opens a PR in the foreground. The gh-issue handler has no execute path; the documented story is "pull the issue manually." After the planned `/do-tasks` consolidation lands, gh-issue execution remains unsupported.

This may be a **deliberate non-goal**: gh-issue was scoped as a capture/list destination, and the project may intentionally route tracker execution only through Linear (its richer claim/state model).

## Task

1. Decide whether `/do-tasks` (single, foreground) should support the gh-issue handler.
2. If **build**: define what's involved — an unclaimed-issue query, an atomic claim (assignee + label/state guard against parallel claims), a branch name the handler publishes, and a PR linked back so merge closes the issue. This mirrors the Linear claim path in `commands/handlers/linear-claim.md`; a `commands/handlers/gh-issue-claim.md` would likely be the home.
3. If **non-goal**: mark the cell in the `commands/task-config.md` matrix as a deliberate non-goal and note in `commands/handlers/gh-issue.md` that execution stays manual / Linear-only, with rationale.

## Acceptance Criteria

- The decision is recorded: either a gh-issue single-execute path is implemented (matrix updated to supported), or the capability matrix in `commands/task-config.md` and `commands/handlers/gh-issue.md` explicitly mark gh-issue execute as a deliberate non-goal with rationale.
- `just check` passes.
