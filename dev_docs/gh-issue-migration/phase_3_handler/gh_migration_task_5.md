---
title: Carry the label state model through add, list, promote and do
priority: high
size: 5
status: done
created: 2026-08-24
source_branch: bestdan/gh-issue-migration-plan
parent: gh_migration
related_files:
  - commands/handlers/gh-issue.md
  - commands/handlers/gh-issue-promote.md
  - commands/handlers/gh-issue-complete.md
tags: [handler, state-model]
---

← [gh_migration_plan.md](../gh_migration_plan.md)

**Done — PR #439, merged `a4815d7`.**

# Carry the label state model through add, list, promote and do

## Context

The lifecycle `new → ready → started → needs_review → done` has no native home
on GitHub. It lives in labels (task 3), with **done implicit**: a done task is a
_closed_ issue carrying no `status:` label. Estimate and priority labels survive
closure.

`/promote-tasks` gates on `est:` — `finplan`'s config carries four hand-written
`max_estimate` overrides, so the gate is real and in use. `auto:eligible` /
`auto:human-review-needed` is an explicit pair so an unclassified issue is
visibly unclassified and drops out of both queues rather than defaulting into
one.

## Task

Update the `gh-issue` handler commands to read and write state through labels:

- `/add-task` creates with `status:0_untriaged` and `auto:human-review-needed`.
- `/list-tasks` renders status, priority, estimate, and blockers from labels plus
  the native dependency endpoints.
- `/promote-tasks` scores and moves `0_untriaged` → `2_ready` or
  `1_needs_refinement`, gating on `est:` against `max_estimate`.
- `/complete-task` closes the issue and strips the `status:` label.
- Dependency-ready means: `status:2_ready` and no open issue in
  `dependencies/blocked_by`.

All writes go through the task 3 helper.

## Acceptance Criteria

**Code-enforced**

- A test asserts a closed issue retains `est:`/`prio:` and carries no `status:`
- A test asserts dependency-readiness excludes an issue whose blocker is still open
- A test asserts `/promote-tasks` respects a per-project `max_estimate`

**User-run**

- Run the full loop on one real `workflow-skills` issue: add → promote → do → PR → merge → confirm closed with estimate intact
