---
title: "cleanup: graduate Stage-0 findings into permanent docs, delete this plan's scaffolding"
priority: medium
size: 1
status: new
created: 2026-07-21
source_branch: bestdan/autopilot-e-lite-design
assignee: bestdan
related_files:
  - dev_docs/auto-pilot-e-lite-design-2026-07-21.md
is_blocked_by: elite_stage0_task_11
parent: elite_stage0
tags: [e-lite, stage-0, cleanup]
---

Plan: [[elite_stage0_plan]]

## Context

Plan-lifecycle rule: `<name>_plan/` folders are temporary tracker scaffolding, not documentation. Once the measured revision is merged and the re-plan checkpoint ([[elite_stage0_task_11]]) has produced the next tranche's plan, this plan is done.

## Task

- Confirm durable wisdom already lives in permanent docs: the measured revision (task 8's PR) and the spike evidence directory. Move anything load-bearing that only exists in these task files (decisions, gotchas, redirect outcomes) into `dev_docs/` — likely a short addendum to the measured revision rather than a new doc.
- Mark the epic `status: done` and delete `dev_docs/tasks/elite_stage0_plan/`.
- Do **not** delete the spike evidence directory — it is the checked-in measurement record the design cites.

## Acceptance Criteria

- `dev_docs/tasks/elite_stage0_plan/` no longer exists; no dangling references to it from `dev_docs/`.
- Spike evidence directory and measured revision remain intact.
