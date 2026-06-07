---
title: Add /push-plan gh-issue and jira push paths
priority: low
size: 3
status: ready
created: 2026-06-07
source_branch: bestdan/task/task_14a
related_files:
  - commands/push-plan.md
  - commands/handlers/gh-issue.md
  - commands/handlers/jira.md
is_blocked_by: task_14b
tags:
  - task-loop
  - planning
  - handlers
expires: 2026-07-07
---

> Part of [[task_loop_improvements_plan]]. Extends the `/push-plan` command from
> task_14b to the two trackers whose execution isn't automated. See
> `plan_tracker_sync_design.md` §3 and "Implementation breakdown".

## Context

task_14b builds `/push-plan` with the Linear path. gh-issue and jira need their
own grouping containers and blocker translation, and both require **new handler
capabilities** not present in today's add-flows (the main reason the spike split
this out). Their value is capped because `/do-tasks` execution is Linear-only —
a gh-issue/jira push produces a board you then work manually — so this is lower
priority than 14b. See `plan_tracker_sync_design.md` §3.1 / §3.3 for the mapping
tables and open question O2 (milestone vs `plan:<name>` label).

## Task

1. **gh-issue path** in the `/push-plan` flow: group the plan under a
   **milestone** named after the epic (assign every child issue to it), or fall
   back to a shared `plan:<name>` label if milestone creation is judged out of
   scope — document the downgrade (spike O2). Create one issue per task via
   `commands/handlers/gh-issue.md`. Encode blockers as a `Blocked by: #<number>`
   body footer (gh-issue has no native dependency edge).
2. **jira path**: create the overview as a Jira **Epic** and set each child
   ticket's `parent` to its key. Create all issues first, then a **second pass**
   adds "is blocked by" issue links via the Jira link API (jira's create-flow has
   no link parameter today — this is the new capability).
3. Reuse the topological-order push, slug→id map, and create-missing-only
   idempotency from task_14b — only the per-handler container and blocker
   translation differ. Record `tracker_id` / `tracker_url` back the same way.
4. Update `commands/push-plan.md` so gh-issue and jira no longer fall into the
   "handler not supported for push" branch.

## Acceptance Criteria

- **Code-enforced:** `just check` passes.
- **User-run:** With `handler: gh-issue`, `/push-plan <name>` creates one issue
  per task grouped under a milestone (or the documented label fallback), with
  `Blocked by: #N` footers matching the plan's `is_blocked_by`; re-running creates
  no duplicates. With `handler: jira`, it creates an epic with child tickets and
  native "is blocked by" links matching the plan's dependencies.
