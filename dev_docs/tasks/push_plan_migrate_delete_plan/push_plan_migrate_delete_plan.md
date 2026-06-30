---
type: epic
title: "/push-plan migrates the overview then deletes local plan files"
status: active
owner: Daniel Egan
created: 2026-06-29
---

# /push-plan: migrate-then-delete

## Goal

Make `/push-plan` enforce a single source of truth per handler. After a plan's
tasks are pushed to an external tracker (Linear / gh-issue / Jira), the local
plan files are **deleted** rather than kept — so stale, uncommitted plan files
can't leak across branches and confuse other agents or bloat context. The plan
overview (currently lost on push) is first written into the tracker container's
description so nothing is lost.

## Scope / non-goals

- **In scope:** the three external-tracker paths in `commands/push-plan.md`
  (Linear §4, gh-issue §5, Jira §5b) — pushing the overview body into the
  container description, deleting each migrated task file, deleting the epic file
  + plan directory once everything is migrated, and the supporting report /
  idempotency-doc rewrites. Plus the user-facing doc notes in `plan-with-docs`
  and `task` SKILLs.
- **Not in scope — `repo-pr` handler:** unchanged. `/push-plan` is a no-op there;
  the files _are_ the source of truth, so nothing is migrated or deleted.
- **Not in scope:** updating or deleting _remote_ issues (v1 still never does),
  re-pushing a fully-migrated plan (there's nothing left locally — new work goes
  straight to the tracker via `/add-task`), and staging/committing the deletions
  (left to the user, matching the skill's existing "don't stage or commit").

## Approach

Migrate-then-delete, gated on **confirmed migration**:

- A **task file** is deleted the moment it has a `tracker_id` (its full body now
  lives in the issue). Held (`--ready-only`) and failed (no `tracker_id`) files
  stay. Per-file, as confirmed — not all-or-nothing.
- The **epic file** + plan dir are deleted only when _every_ task is migrated AND
  the epic has a **description-bearing `tracker_id`** (a real project / milestone
  / Epic we created with the overview in its description). A `label:` fallback or
  a reused `default_project` (overview never written) → keep the epic, warn.
- **Key interaction** (the main tradeoff considered): deleting a pushed task that
  is a blocker of a _held_ task would strand the held task's blocker reference
  (the slug no longer names an in-plan file). Fix: before deleting task A,
  rewrite every _kept_ task's `is_blocked_by: <A-slug>` → `<A-tracker-id>`.
  Because a tracker-id entry is a pass-through in both the topological-order step
  and the native-link step, the held task's `blockedBy` link is preserved with no
  new manifest file and no spurious "unknown slug" warning. Rejected alternative:
  a `.pushed-map.yml` manifest (more surface, more state); also rejected: accept
  lost links and only warn (the rewrite is nearly free and strictly better).

## Tasks

1. [[push_plan_migrate_delete_task_1]] — Write the plan overview into the tracker
   container description at create time (Linear project, gh-issue milestone;
   Jira Epic already does this).
2. [[push_plan_migrate_delete_task_2]] — Delete each migrated task file, with the
   kept-dependent `is_blocked_by` slug→id rewrite; add the "Deleted locally"
   report section.
3. [[push_plan_migrate_delete_task_3]] — Delete the epic file + plan directory
   behind the description-bearing-`tracker_id` + all-tasks-migrated gate; warn and
   keep on the label-fallback / reused-`default_project` cases; rewrite the §6
   idempotency section and add the "Kept locally" report section.
4. [[push_plan_migrate_delete_task_4]] — Update the user-facing docs
   (`plan-with-docs` and `task` SKILLs) to state that pushing a plan to a tracker
   deletes the local files and that the tracker is then the only source of truth.

## Open questions

None outstanding — the delete-scope (per-file), overview-content (full body),
delete-safety (hard delete on confirmed migration), and blocker-rewrite decisions
were all settled during brainstorming.
