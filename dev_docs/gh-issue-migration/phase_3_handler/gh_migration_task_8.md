---
title: Upgrade reoptimize from report-only to native dependency edges
priority: low
size: 3
status: new
created: 2026-08-24
source_branch: bestdan/gh-issue-migration-plan
parent: gh_migration
is_blocked_by: gh_migration_task_5
related_files:
  - commands/handlers/gh-issue-reoptimize.md
tags: [handler, dependencies]
---

← [gh_migration_plan.md](../gh_migration_plan.md)

# Upgrade reoptimize from report-only to native dependency edges

## Context

`gh-issue-reoptimize.md` is report-only because GitHub Issues was believed to
have no native dependency edge — findings surfaced as a suggested
`Blocked by:` body footer rather than a real link.

**That is no longer true.** The spike confirmed `POST` to
`/issues/{n}/dependencies/blocked_by` creates a real edge that reads back, on a
personal-account repo. Sub-issues likewise. So the report-only limitation is now
a handler gap, not a platform one.

## Task

Rewrite `gh-issue-reoptimize.md` to apply dependency edits natively rather than
reporting them: create and remove `blocked_by` edges, detect cycles across the
real graph, and drop the body-footer convention entirely.

Migrate any existing `Blocked by:` footers in `workflow-skills` issue bodies into
native edges as part of this task.

## Acceptance Criteria

**Code-enforced**

- A test asserts a dependency finding results in a native edge, not a body edit
- A test asserts cycle detection reads the native graph
- No `Blocked by:` footer-writing code path remains

**User-run**

- Run `/reoptimize-tasks` against the migrated `workflow-skills` backlog; spot-check three edges in the GitHub UI
