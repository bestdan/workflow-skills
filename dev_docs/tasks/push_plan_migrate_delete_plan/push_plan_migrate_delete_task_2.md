---
title: "push-plan: delete each migrated task file with kept-dependent blocker rewrite"
priority: high
size: 3
status: new
created: 2026-06-29
source_branch: main
related_files:
  - commands/push-plan.md:139   # §4.4 Linear — create issues, slug→id map
  - commands/push-plan.md:241   # §5.4 gh-issue — create issues
  - commands/push-plan.md:337   # §5b.4 Jira — create issues
  - commands/push-plan.md:362   # §5b.5 Jira — second-pass native links
  - commands/push-plan.md:390   # §7 Report
is_blocked_by: push_plan_migrate_delete_task_1
parent: push_plan_migrate_delete
expires: 2026-09-29
tags:
  - scope: docs
---

> Plan: [[push_plan_migrate_delete_plan]]

## Context

The three tracker paths in `commands/push-plan.md` each walk tasks in topological
order, create an issue per task, and **record `tracker_id` back** into the task
file (§4.4 step 5, §5.4 step 5, §5b.4 step 5). After this task, a task file is
**deleted** the moment its migration is confirmed (its `tracker_id` is written and
the create call returned a URL) — its full `## Context` / `## Task` /
`## Acceptance Criteria` body now lives in the issue.

The non-obvious interaction (settled in brainstorming): if pushed-then-deleted
task **A** is the blocker of a **held** task **B** (`B.is_blocked_by: A`, held by
`--ready-only` or by a mid-run failure), then once A's file is gone, B's later
re-push can't resolve the slug `A` — the topological-order step (§4.3) and the
seed step (§4.4) only know in-plan slugs **from files on disk**. B would mis-warn
"unknown slug (typo?)" and lose its native `blockedBy` link.

**Fix:** before deleting A's file, rewrite every **kept** task file whose
`is_blocked_by` contains the slug `A` so that entry becomes A's `tracker_id`
(preserving single-value vs list shape). A tracker-id entry is already a
pass-through in §4.3 (matches the "already an id" pattern → not an ordering edge)
and in §4.4/§5.4/§5b.5 (handed straight to the native-link collector), so B's link
survives with no new manifest and no spurious warning.

`is_blocked_by` "already an id" shapes per handler (reuse the existing patterns):
Linear `/^[A-Z]+-\d+$/` (§4.3), gh-issue `/^(\S*#)?\d+$/` (§5.3), Jira
`/^[A-Z][A-Z0-9]*-\d+$/` (§5b.3).

This task deletes **task files only**. The epic file + directory deletion and the
all-migrated gate are task 3.

## Task

In `commands/push-plan.md`:

1. **Add a shared "Cleanup (delete migrated task files)" sub-step** — author it
   once (e.g. under §4 as the canonical path) and have §5 (gh-issue) and §5b
   (Jira) reference it the same way they already reference §4.3's ordering. The
   sub-step, run **after** a task's `tracker_id` is recorded:
   - **Confirm migration:** the create call returned a URL/id and `tracker_id` was
     written back. If the create errored (no `tracker_id`), keep the file.
   - **Rewrite kept dependents first:** for the just-migrated task with slug `S`
     and id `ID`, find every _still-present_ task file in the plan whose
     `is_blocked_by` contains `S` and rewrite that entry to `ID` (preserve
     single-value vs list shape; translate only the matching entry).
   - **Delete** the migrated task file. Hard delete (the body is in the issue).
   - Held (`--ready-only`) and failed (no `tracker_id`) files are **not** deleted.
2. **Wire the Jira second pass (§5b.5):** because Jira links are drawn after all
   issues exist, the kept-dependent rewrite and the per-file deletion for Jira must
   run **after** §5b.5 completes (so the link pass still sees every blocker key),
   not inline in §5b.4. Note this ordering explicitly.
3. **§7 Report — add a "Deleted locally" section:** one line per removed task file
   with its `tracker_id` and URL.

## Acceptance Criteria

- A shared cleanup sub-step deletes a task file once its `tracker_id` is confirmed,
  and is referenced by the Linear, gh-issue, and Jira paths.
- Before deletion, kept dependents' `is_blocked_by` slug entries for the deleted
  task are rewritten to its `tracker_id`, preserving single vs list shape.
- Held (`--ready-only`) and failed (no `tracker_id`) task files are explicitly **not**
  deleted.
- The Jira path runs cleanup **after** the §5b.5 native-link pass.
- §7 reports a "Deleted locally" section listing each removed file with its id/URL.
- **Code-enforced:** `just check` passes (dprint + `claude plugin validate .
  --strict` + `scripts/validate.py`).
