---
title: Add /push-plan command — repo-pr no-op + Linear push path
priority: medium
size: 3
status: ready
created: 2026-06-07
source_branch: bestdan/task/task_14a
related_files:
  - skills/plan-with-docs/SKILL.md
  - commands/add-task.md
  - commands/handlers/linear-add.md
  - commands/do-tasks.md
is_blocked_by: task_14a
tags:
  - task-loop
  - planning
expires: 2026-07-07
---

> Part of [[task_loop_improvements_plan]]. Implements the high-value path from
> the design spike `plan_tracker_sync_design.md` (§1–§4). Depends on the
> `tracker_id` / `tracker_url` schema fields landed in task_14a (the schema PR);
> that card has no file because it was completed in the PR that created this one.

## Context

`plan-with-docs` always writes file-based tasks regardless of the configured
handler, so teams on Linear/Jira get a split: `/add-task` files to the tracker
but whole plans land as repo files the tracker never sees. The spike settled the
flow: **local-first** — draft and vet the plan locally, then _explicitly_ push
the vetted plan to the configured tracker via a new `/push-plan <name>` command;
never auto-sync on write. This card builds the command skeleton plus the
`repo-pr` no-op and the Linear path (the highest-value path, since `/do-tasks`
executes Linear issues automatically). gh-issue and jira are deferred to
task_14c. See `plan_tracker_sync_design.md` for the full rationale and rejected
alternatives.

## Task

1. Create `commands/push-plan.md` (`/push-plan <name>`): resolve the handler off
   `.task-config.yml` exactly like `/add-task` / `/do-tasks`. `repo-pr` (or
   absent) ⇒ **no-op** that prints "repo-pr handler: plans already live as task
   files — no external tracker to push to" and stops.
2. **Readiness check, push-by-default** (spike §2): classify the plan's non-epic
   task files into `ready` vs not-ready, show the user the summary plus each
   not-ready task's "what's needed", confirm, then push the whole plan.
   `--ready-only` pushes just the `ready` subset. The epic file (`type: epic`)
   is the container, not a task.
3. **Linear path** (spike §3): map the overview epic → a Linear **project**
   (reuse `linear.default_project` or create one named after the epic title;
   record its id on the epic file). Create one issue per `task_N.md` by reusing
   `commands/handlers/linear-add.md` — do **not** duplicate create logic.
4. **Blocker translation** (spike §3.3): push in topological order, maintain a
   live slug→tracker-id map (seed it from already-pushed tasks' `tracker_id` on
   re-push), translate each task's `is_blocked_by` slugs to Linear identifiers
   before handing the drafted task to `linear-add` (which already renders native
   `blockedBy` when the value matches `/^[A-Z]+-\d+$/`). Pass through entries
   already shaped like ids or absent from the map. A cycle is a plan bug — stop.
5. **Idempotency** (spike §4): write `tracker_id` (+ optional `tracker_url`) back
   into each created file's frontmatter; re-push is **create-missing-only** (skip
   files that already have a `tracker_id`). Reuse a recorded container id instead
   of creating a duplicate.
6. Update `skills/plan-with-docs/SKILL.md` to document local-first → vet → push,
   that plans still draft to local files by default, and a one-line pointer to
   `/push-plan` from the plan-review step. Add `/push-plan` to the README command
   table and bump the component count (enforced by `scripts/validate.py`).

## Acceptance Criteria

- **Code-enforced:** `just check` passes; `commands/push-plan.md` exists with
  valid frontmatter; README count updated.
- **User-run:** With `handler: linear` and a vetted plan, `/push-plan <name>`
  creates one Linear issue per task with blocker relationships matching the
  plan's `is_blocked_by`, grouped under a project; running it again creates no
  duplicates. With `handler: repo-pr`, it is a no-op and says so. Plans still
  draft to local files by default — nothing pushes without the explicit command.
