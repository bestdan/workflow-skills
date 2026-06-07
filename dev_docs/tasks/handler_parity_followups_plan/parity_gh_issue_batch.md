---
title: Decide gh-issue batch-process support or record it as a non-goal
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

Matrix cell: **gh-issue × process / batch** (`/do-tasks --all`).

Batch dispatch (`/do-tasks --all` / `-n N`, bounded by `wip_limit`) drains a known-ready backlog by dispatching multiple remote agents. It is implemented for the file-based `repo-pr` handler only. No non-`repo-pr` handler has a batch path, so gh-issue batch is unsupported after the planned parity work.

This may be a **deliberate non-goal**: batch fan-out across remote VMs is built on the file-store mechanics (deterministic `task/<slug>` branches, file deletion as the done signal), and trackers manage bulk work through their own UIs. Depends on whether single-execute (see [[parity_gh_issue_execute]]) is even built first.

## Task

1. Decide whether `/do-tasks --all` should support the gh-issue handler. This is downstream of [[parity_gh_issue_execute]] — batch without a single-execute path is moot.
2. If **build**: define what's involved — selecting dependency-ready unclaimed issues, applying `wip_limit`, and dispatching remote agents that each claim atomically (the push-race guard differs from the file store). Scope honestly; this is likely larger than a single card and may need splitting.
3. If **non-goal**: mark the cell in the `commands/task-config.md` matrix as a deliberate non-goal and note in `commands/handlers/gh-issue.md` that batch stays `repo-pr`-only, with rationale.

## Acceptance Criteria

- The decision is recorded: either a gh-issue batch path is implemented (matrix updated to supported), or the capability matrix in `commands/task-config.md` and `commands/handlers/gh-issue.md` explicitly mark gh-issue batch as a deliberate non-goal with rationale.
- `just check` passes.
