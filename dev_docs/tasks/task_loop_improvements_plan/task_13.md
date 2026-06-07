---
title: Design spike — local-first plan→tracker sync
priority: medium
size: 3
status: new
created: 2026-06-07
source_branch: claude/keen-tesla-pgLI4
related_files:
  - skills/plan-with-docs/SKILL.md
  - commands/add-task.md
  - commands/handlers/linear-add.md
  - commands/handlers/jira.md
  - commands/handlers/gh-issue.md
tags:
  - task-loop
  - planning
  - scope: research
  - design
expires: 2026-07-07
---

> Part of [[task_loop_improvements_plan]]. Spike feeding [[task_14]].

## Context

`plan-with-docs` always writes file-based tasks regardless of the configured handler, so teams on Linear/Jira get a split: `/add-task` files to the tracker but plans land as repo files. The desired flow (from planning) is **local-first**: draft and vet the plan locally, *then* explicitly push the vetted plan to the configured tracker — not auto-sync on write. This task is a design spike to settle the real questions before building (no production behavior change).

Questions to answer:

- **Trigger:** a new `/push-plan <name>` command vs a `plan-with-docs --push` flag vs a prompt at the end of plan review. Recommend one.
- **Mapping:** plan overview → epic/project/parent? Each `task_N.md` → one tracker issue via the existing handler add-flow? How is `is_blocked_by` (slug) translated to tracker blocker relationships (Linear native `blockedBy`, Jira issue links)?
- **Idempotency / re-push:** how to avoid duplicate issues when a plan is pushed twice (record tracker ids back into the local files? a manifest?). How are local edits-after-push reconciled?
- **Vetting gate:** what marks a plan "vetted enough to push" (all tasks `status: ready`? explicit user confirm?).
- **Reverse drift:** out of scope to fully solve, but note how status set in the tracker relates to the now-pushed local files (do local files become read-only/stale, or get deleted?).

## Task

1. Write `dev_docs/tasks/task_loop_improvements_plan/plan_tracker_sync_design.md` answering the questions above with a recommended approach and the main rejected alternative for each.
2. Produce a concrete task breakdown for the implementation (what becomes [[task_14]], and whether it needs splitting per handler).
3. Flag any prerequisite (e.g. depends on the `/do-tasks` tracker path, or on storing tracker ids in frontmatter — which would need a schema follow-up).

## Acceptance Criteria

- **User-run:** `plan_tracker_sync_design.md` exists and gives a single recommended trigger, mapping, and idempotency strategy, each with rationale; lists the implementation tasks with sizes; names any new frontmatter field or command required.
- No production command/skill behavior changes in this task (design only).
