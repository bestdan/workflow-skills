---
title: Add /push-plan jira push path
priority: low
size: 2
status: in_progress
created: 2026-06-07
source_branch: claude/vigilant-ritchie-g3Z4W
related_files:
  - commands/push-plan.md
  - commands/handlers/jira.md
tags:
  - task-loop
  - planning
  - handlers
expires: 2026-07-07
---

> Part of [[task_loop_improvements_plan]]. Carved out of the original task_14c,
> whose **gh-issue half shipped** on `claude/vigilant-ritchie-g3Z4W`; this is the
> jira half. Split out because the jira path needs a host with a **working jira
> CLI** (the environment that built the gh-issue path has none), so it can't be
> verified here. See `plan_tracker_sync_design.md` §3.1 / §3.3 and open question
> O2.

## Context

`/push-plan` (`commands/push-plan.md`) now pushes a vetted plan to `linear`
(native project + `blockedBy` links) and `gh-issue` (milestone grouping +
`Blocked by: #<number>` footer). `jira` still falls into the §1 "not yet
supported" branch. Jira needs its own grouping container and blocker translation,
and both require **new handler capabilities** not present in today's jira
add-flow (`commands/handlers/jira.md` has create, but no epic-parent or
issue-link step) — the main reason the spike split this out. Its value is capped
because `/do-tasks` execution is Linear-only, so a jira push produces a board you
then work manually. **This path must be implemented and verified on a machine
with an authenticated jira CLI** — it cannot be exercised in the gh-issue build
environment.

The gh-issue path already established the shared scaffolding to reuse verbatim:
topological-order push (§4.3/§5.3), the live slug→tracker-id map, and
create-missing-only idempotency (§6). Only the per-handler container and blocker
translation differ.

## Task

1. **jira path** in the `/push-plan` flow (add a `## 5b`-style section to
   `commands/push-plan.md`, parallel to §5 gh-issue): create the overview epic as
   a Jira **Epic** and set each child ticket's `parent` to its key. Create all
   issues first, then a **second pass** adds "is blocked by" issue links via the
   Jira link API (jira's create-flow has no link parameter today — this is the new
   capability). Add the link step to `commands/handlers/jira.md`.
2. Reuse the topological-order push, slug→id map, and create-missing-only
   idempotency from the gh-issue/Linear paths — only the container (epic) and
   blocker translation (native issue links) differ. Record `tracker_id` /
   `tracker_url` back the same way; the epic file records the Jira epic key.
3. Update `commands/push-plan.md` §1 so `jira` no longer falls into the "handler
   not supported for push" branch.

## Acceptance Criteria

- **Code-enforced:** `just check` passes.
- **User-run (requires an authenticated jira CLI host):** With `handler: jira`,
  `/push-plan <name>` creates an epic with child tickets and native "is blocked
  by" links matching the plan's `is_blocked_by`; re-running creates no duplicates
  (create-missing-only).
