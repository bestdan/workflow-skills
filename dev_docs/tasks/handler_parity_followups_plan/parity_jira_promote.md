---
title: Decide jira promote support or record it as a non-goal
priority: low
size: 3
status: new
created: 2026-06-07
source_branch: bestdan/handler-parity-followups
related_files:
  - commands/task-config.md
  - commands/handlers/jira.md
  - commands/promote-tasks.md
expires: 2026-09-07
tags:
  - task-loop
  - handlers
  - parity
---

> Part of [[handler_parity_followups_plan]].

## Context

Matrix cell: **jira × promote** (`/promote-tasks`).

`/promote-tasks` scores `status: new` task files and flips them to `ready` / `needs_refinement`. It is file-only; the planned parity work only adds a Linear promote path. After it lands, the jira handler still has no promote verb. Jira items are created already-actionable, and Jira's own workflow/status scheme is the natural place for a triage gate.

This may be a **deliberate non-goal**: Jira manages issue states through its configured workflow, and a repo-side promotion scorer may duplicate or conflict with that.

## Task

1. Decide whether `/promote-tasks` should support the jira handler at all.
2. If **build**: define what "promote" means for a Jira item (e.g. a workflow transition driven by the same confidence check) and what would be involved in `commands/promote-tasks.md` + `commands/handlers/jira.md`.
3. If **non-goal**: mark the cell in the `commands/task-config.md` capability matrix as a deliberate non-goal (not merely "unsupported") and note the rationale in `commands/handlers/jira.md`.

## Acceptance Criteria

- The decision is recorded: either a jira promote path is implemented (matrix updated to supported), or the capability matrix in `commands/task-config.md` and `commands/handlers/jira.md` explicitly mark jira promote as a deliberate non-goal with rationale.
- `just check` passes.
