---
title: Graduate & clean up — fold decisions into dev_docs, delete plan scaffolding
priority: low
size: 1
status: ready
created: 2026-07-17
expires: 2026-08-16
source_branch: claude/sleepy-ride-8d4bjx
related_files:
  - dev_docs/tasks/deterministic-code-opportunity.md # the permanent home for durable decisions
  - dev_docs/tasks/deterministic_scripts_plan/ # the scaffolding to delete
is_blocked_by: deterministic_scripts_task_1
parent: deterministic_scripts
tags: [cleanup, docs]
---

# Task 6 — Graduate the plan, delete the scaffolding

Part of [[deterministic_scripts_plan]]. The plan-lifecycle cleanup task. Blocked
by all the build/close-out tasks — do this **last**, once tasks 1–5 have merged.

> **Note:** `is_blocked_by` lists `deterministic_scripts_task_1` as the anchor,
> but this task should only run once **all** of tasks 1–5 and 7 are `done`. When
> you pick it up, verify the rest are also complete (or add them to
> `is_blocked_by` at push time — the repo-pr readiness rule requires *every*
> listed blocker to be done, so extend the list if you want the gate to enforce
> it).

## Context

A `<name>_plan/` folder is temporary project-tracker scaffolding, not permanent
documentation. Once the scripts are built and the audit is reconciled, the
durable wisdom belongs in a top-level `dev_docs/` doc and the scaffolding should
go. The natural permanent home already exists:
`dev_docs/tasks/deterministic-code-opportunity.md` (the audit itself) — its
Findings sections become the record of what was decided and why.

## Task

1. Fold any durable decisions produced during execution (final script
   interfaces, load-bearing tradeoffs, gotchas a future dev needs — e.g. the
   consumer-repo path-resolution rule from task 3, the whole-line-match
   discipline from task 4, the "repo-pr only, Linear covered separately"
   boundary) into `dev_docs/tasks/deterministic-code-opportunity.md` as a short
   "Delivered" addendum, or a new `dev_docs/deterministic-scripts.md` if the
   audit doc is getting unwieldy.
2. Mark each delivered Finding in the audit as shipped (cross-reference the
   merged PRs).
3. Delete the `dev_docs/tasks/deterministic_scripts_plan/` folder (this plan's
   scaffolding) and any design/notes files it spawned. `dev_docs/tasks/` should
   hold only live scaffolding — never the residue of a finished plan.
4. Set the plan epic (`deterministic_scripts_plan.md`) `status: done` before
   deleting, or note completion in the graduated doc.

## Acceptance Criteria

**Code-enforced:**

- `scripts/check.sh` passes (`dprint check`).
- `dev_docs/tasks/deterministic_scripts_plan/` no longer exists after this task.

**User-run:**

- Confirm the durable decisions survived the move (they're in a permanent
  `dev_docs/` doc, not only in the deleted plan files), and the audit doc marks
  the delivered findings with their PR references.
