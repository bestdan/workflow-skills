---
title: Graduate durable design to dev_docs/cao_usage.md and delete plan scaffolding
priority: low
size: 1
status: blocked
created: 2026-07-15
source_branch: worktree-bestdan+cao-usage-adapter
related_files:
  - dev_docs/tasks/cao_usage_plan/
is_blocked_by: [cao_usage_task_2, cao_usage_task_3, cao_usage_task_4, cao_usage_task_5]
parent: cao_usage
tags: [cleanup, docs]
---

Part of [[cao_usage_plan]].

## Context

Per `plan-with-docs` lifecycle, a `<name>_plan/` folder is temporary scaffolding. Once tasks 1–5 land, the durable architecture (the reuse boundary, the two changed seams, the reserve/pause semantics, and the load-bearing decisions from the epic's Resolved decisions) must graduate to a permanent doc, and the scaffolding must be deleted so `dev_docs/tasks/` holds only live plans.

## Task

- Write `dev_docs/cao_usage.md` capturing, tersely: the reuse boundary (what came from auto-pilot unchanged vs what was added), the two changed seams (plan-adapter source, CAO custom-coder dispatch), the reserve/pre-invoke-gate/`reset_epoch`/grace semantics, and the resolved decisions (record Depth 1 and why).
- Delete `dev_docs/tasks/cao_usage_plan/` and any design/notes files this plan spawned.

## Acceptance Criteria

**Code-enforced**
- `dev_docs/cao_usage.md` exists and covers the reuse boundary + the resolved decisions.
- `dev_docs/tasks/cao_usage_plan/` no longer exists.

**User-run**
- A reader new to the change can, from `dev_docs/cao_usage.md` alone, explain why pause/resume was reused rather than rebuilt and which dispatch depth shipped.
