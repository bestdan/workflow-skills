---
title: "Docs: note that pushing a plan deletes the local files (tracker is sole source of truth)"
priority: medium
size: 1
status: new
created: 2026-06-29
source_branch: main
related_files:
  - skills/plan-with-docs/SKILL.md:83   # step 7 report — the /push-plan pointer
  - skills/plan-with-docs/SKILL.md:92   # Rules — local-first paragraph
  - skills/task/SKILL.md                 # Epics / push-plan references
is_blocked_by: push_plan_migrate_delete_task_3
parent: push_plan_migrate_delete
expires: 2026-09-29
tags:
  - scope: docs
---

> Plan: [[push_plan_migrate_delete_plan]]

## Context

`plan-with-docs` currently tells users that plans are **local-first** and that
`/push-plan` "syncs" a plan to the tracker, implying the local files persist after
a push (step 7 report pointer, and the "local-first" Rules paragraph). With the
migrate-then-delete behavior (tasks 1–3), that's no longer true for external
trackers: a successful push **deletes** the local plan files and the tracker
becomes the sole source of truth. The user-facing docs should say so, so nobody
expects the local plan dir to stick around after pushing.

`skills/task/SKILL.md` is referenced by `push-plan` for the **Epics** model and
the task format; check whether it makes any "plans persist locally" claim that now
needs the same caveat (only update if such a claim exists — don't pad).

## Task

1. **`skills/plan-with-docs/SKILL.md`:** update the step-7 `/push-plan` pointer and
   the "local-first" Rules paragraph to add one line: on a tracker handler, a
   successful `/push-plan` writes the overview into the tracker container and then
   **deletes** the migrated local files — the tracker is then the only source of
   truth (the `repo-pr` handler is unchanged: files stay, they _are_ the source of
   truth). Keep the existing local-first framing (plans are still drafted to files
   first; pushing is still a separate explicit step).
2. **`skills/task/SKILL.md`:** if (and only if) it states or implies that pushed
   plans persist locally, add the same one-line caveat; otherwise leave it.

## Acceptance Criteria

- `plan-with-docs/SKILL.md` states that a successful tracker push deletes the local
  plan files and that `repo-pr` is exempt.
- `task/SKILL.md` is consistent — updated only if it carried a now-stale "plans
  persist" claim.
- **Code-enforced:** `just check` passes (dprint + `claude plugin validate .
  --strict` + `scripts/validate.py`).
