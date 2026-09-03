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
real graph, and drop the body-footer convention entirely — **but read the
constraint below before acting on that last clause.**

Migrate any existing `Blocked by:` footers in `workflow-skills` issue bodies into
native edges as part of this task.

## Constraint — dropping the footer takes the last signal a routine can see

Recorded 2026-09-02, from task 15 (PR #444).

**A cloud routine cannot read the native dependency graph in any form.** It has
no `gh`, raw HTTP carries no credential, and the GitHub MCP connector has no
dependency tool — measured 2026-08-24,
[`2026-08-24-routine-claim-channel.md`](../../decisions/2026-08-24-routine-claim-channel.md).
Both `gh-issue-ready.py` (read) and `gh-issue-deps.py` (write, new in task 15)
shell out to `gh`, so the whole dependency path is local-only.

Issue **bodies**, however, are readable through the connector. So
`Blocked by: #<n>` is the only blocked-ness signal available **to a routine**, and
"drop the body-footer convention entirely" removes it.

> **Amended 2026-09-02 — "unattended" is wider than "routine".** This section
> originally said the footer was the only blocked-ness signal available
> _unattended_. That does not follow. Task 6
> ([PR #447](https://github.com/bestdan/workflow-skills/pull/447)) measured a
> **third** credentialed channel: a GitHub Actions runner, which is unattended,
> **has `gh`**, and whose ambient `GITHUB_TOKEN` writes issue labels when the
> workflow declares `permissions: issues: write`.
>
> That does not settle this question either — it removes a premise. The paragraph
> above rests on "unattended ⇒ no `gh`", which is false. Whether a runner can read
> the dependency graph is **unmeasured**: the token reaching the issues API does
> not establish it reaching the dependency / sub-issue endpoints, and
> `gh-issue-ready.py` shelling out to `gh` is no longer disqualifying on its own,
> because a runner has `gh`.
>
> **Probe it before deciding.** One workflow with `issues: read` calling the
> dependency endpoint answers it. If a runner can read the graph, the footer is
> not the last signal and this constraint stops blocking "drop the footer" — the
> unattended reader becomes a scheduled workflow rather than a routine. The token
> is repo-scoped, so that only holds where the tracker is the code's own repo.

This does **not** settle the question — it supplies the fact the decision needs:

- The footer is a **hint, not the graph.** Nothing keeps it in sync with the
  edge, and this flow writes footer lines for dependencies that have no edge at
  all, so a routine reading one can be reading a proposal that was never
  applied. It cannot be sold as a supported unattended dependency check.
- Task 15 kept the footer on the `/push-plan` path as a **human-readable echo of
  the edge** (`push-plan.md` §5, `gh-issue.md` step 2). Dropping it here while
  push-plan still writes it leaves the two paths disagreeing about whether the
  footer means anything — decide for both, not one.
- The acceptance criterion below, "No `Blocked by:` footer-writing code path
  remains", presumes the drop. If the footer stays, that criterion is what
  changes.

The clean resolution is probably to keep writing the footer as an echo and stop
_reading_ it, which is what makes the edge the single source of truth without
blinding the routine channel. Whoever runs this task decides; the point is that
the choice is now visible.

## Acceptance Criteria

**Code-enforced**

- A test asserts a dependency finding results in a native edge, not a body edit
- A test asserts cycle detection reads the native graph
- No `Blocked by:` footer-writing code path remains

**User-run**

- Run `/reoptimize-tasks` against the migrated `workflow-skills` backlog; spot-check three edges in the GitHub UI
