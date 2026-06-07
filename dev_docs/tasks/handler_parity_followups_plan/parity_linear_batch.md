---
title: Decide linear batch-process support or record it as a non-goal
priority: low
size: 3
status: new
created: 2026-06-07
source_branch: bestdan/handler-parity-followups
related_files:
  - commands/task-config.md
  - commands/handlers/linear-claim.md
  - commands/do-tasks.md
expires: 2026-09-07
tags:
  - task-loop
  - handlers
  - parity
---

> Part of [[handler_parity_followups_plan]].

## Context

Matrix cell: **linear × process / batch** (`/do-tasks --all`).

Linear is the one tracker with a `/do-tasks` execution path, but that path is single-claim only: `/do-tasks --all` against the Linear handler explicitly degrades to a single foreground claim. Batch fan-out across remote VMs is `repo-pr`-only. So even Linear, the most-supported tracker, has no batch cell after the planned parity work.

This may be a **deliberate non-goal**: the foreground claim model is intentionally one-card-at-a-time (pair on a card with the agent watching), and bulk parallel execution is the file-store's job. Lifting it to Linear means reconciling the foreground claim model with remote fan-out and a non-file push-race guard.

## Task

1. Decide whether `/do-tasks --all` should support the linear handler (true batch) rather than degrading to a single claim.
2. If **build**: define what's involved — selecting multiple dependency-ready unclaimed Linear issues, applying `wip_limit`, dispatching remote agents that each claim atomically, and reconciling with the existing single-claim foreground path in `commands/handlers/linear-claim.md`. Scope honestly; likely larger than a single card.
3. If **non-goal**: confirm and document the current degrade-to-single behavior as intentional — mark the cell in the `commands/task-config.md` matrix as a deliberate non-goal and note the rationale in `commands/handlers/linear-claim.md` (or wherever the `/do-tasks` Linear path is documented).

## Acceptance Criteria

- The decision is recorded: either a linear batch path is implemented (matrix updated to supported), or the capability matrix in `commands/task-config.md` and the Linear handler docs explicitly mark linear batch as a deliberate non-goal (degrade-to-single is intentional) with rationale.
- `just check` passes.
