---
title: Decide jira batch-process support or record it as a non-goal
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

Matrix cell: **jira × process / batch** (`/do-tasks --all`).

Batch dispatch (`/do-tasks --all` / `-n N`, bounded by `wip_limit`) is implemented for the file-based `repo-pr` handler only. No non-`repo-pr` handler has a batch path, so jira batch is unsupported after the planned parity work.

This may be a **deliberate non-goal**: batch fan-out is built on the file-store mechanics, and Jira manages bulk work through its own UI/automation. Downstream of single-execute (see [[parity_jira_execute]]).

## Task

1. Decide whether `/do-tasks --all` should support the jira handler. This is downstream of [[parity_jira_execute]] — batch without a single-execute path is moot.
2. If **build**: define what's involved — selecting dependency-ready unclaimed items, applying `wip_limit`, and dispatching remote agents that each claim atomically. Scope honestly; likely larger than a single card and may need splitting.
3. If **non-goal**: mark the cell in the `commands/task-config.md` matrix as a deliberate non-goal and note in `commands/handlers/jira.md` that batch stays `repo-pr`-only, with rationale.

## Acceptance Criteria

- The decision is recorded: either a jira batch path is implemented (matrix updated to supported), or the capability matrix in `commands/task-config.md` and `commands/handlers/jira.md` explicitly mark jira batch as a deliberate non-goal with rationale.
- `just check` passes.
