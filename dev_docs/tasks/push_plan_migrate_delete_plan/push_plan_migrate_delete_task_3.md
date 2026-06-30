---
title: "push-plan: delete epic file + plan dir behind the migration gate, rewrite idempotency"
priority: high
size: 3
status: new
created: 2026-06-29
source_branch: main
related_files:
  - commands/push-plan.md:109   # §4.2 Linear container resolve (tracker_id write-back)
  - commands/push-plan.md:198   # §5.2 gh-issue container + label fallback
  - commands/push-plan.md:374   # §6 Idempotency
  - commands/push-plan.md:390   # §7 Report
is_blocked_by: push_plan_migrate_delete_task_2
parent: push_plan_migrate_delete
expires: 2026-09-29
tags:
  - scope: docs
---

> Plan: [[push_plan_migrate_delete_plan]]

## Context

After task 2, migrated **task files** are deleted as they're confirmed. This task
deletes the **epic file** and the now-empty plan directory, behind a gate, and
rewrites the idempotency narrative that the old "never deletes local plan files"
behavior described.

The gate (settled in brainstorming): the epic file is deleted only when **both**:

1. **Every** task in the plan is migrated — no held (`--ready-only`) or failed
   files remain in the plan dir, and
2. The epic has a **description-bearing `tracker_id`** — a real project / milestone
   / Epic created with the overview in its description (task 1).

A `tracker_id` is _not_ description-bearing in two cases, where the overview has
no tracker home → **keep the epic file and warn**:

- **gh-issue label fallback** (`tracker_id: label:plan:<name>`, §5.2 step 3) — a
  label has no description field.
- **Reused `default_project`** (Linear §4.2 case 2) — no `tracker_id` is written
  back to the epic and the description was never set by us, so a missing/`label:`
  epic `tracker_id` after an otherwise-complete push signals "overview not
  migrated."

Because the epic is deleted only after _all_ tasks are gone, a kept (held) task's
`parent: <name>` reference never dangles — the epic only disappears when no task
remains to reference it.

## Task

In `commands/push-plan.md`:

1. **Extend the cleanup logic (the §4 shared sub-step from task 2)** with an
   epic-and-directory step that runs after the task-file walk:
   - If any task file remains in the plan dir (held/failed) → **skip** epic
     deletion (the plan isn't fully migrated).
   - Else if the epic's `tracker_id` is description-bearing (a Linear project id /
     gh-issue milestone number / Jira Epic key — i.e. **not** `label:…` and
     present) → **delete the epic file**, then `rmdir` the now-empty
     `<name>_plan/` and any empty `phase_N/` subdirectories.
   - Else (label fallback, or reused `default_project` with no epic `tracker_id`)
     → **keep the epic file** and **warn**: overview kept locally — not migrated
     to a tracker description.
2. **§6 Idempotency — rewrite.** Replace "never deletes the local plan files —
   they keep their `tracker_id` as a traceable back-link" with the new contract:
   each plan file is **deleted once its migration is confirmed**; the tracker is
   then the **only** source of truth; re-pushing a fully-migrated plan is a no-op
   because nothing remains locally — new work for the plan goes straight to the
   tracker via `/add-task`. Keep the still-true guarantees: v1 never updates or
   deletes _remote_ issues; partial pushes (held/failed) re-run safely because
   kept files retain `tracker_id`-resolvable blockers (task 2's rewrite) and the
   container is reused-before-create.
3. **§7 Report — add a "Kept locally" section:** held/failed task files, plus any
   epic kept because its overview had no description home — each with the reason.

## Acceptance Criteria

- Epic file + plan dir are deleted only when all tasks are migrated **and** the
  epic `tracker_id` is description-bearing; empty `phase_N/` dirs are removed too.
- The label-fallback and reused-`default_project` cases keep the epic file and warn.
- §6 is rewritten to the migrate-then-delete contract while preserving the
  "never touches remote issues" and "partial pushes re-run safely" guarantees.
- §7 reports a "Kept locally" section with reasons.
- **Code-enforced:** `just check` passes (dprint + `claude plugin validate .
  --strict` + `scripts/validate.py`).
