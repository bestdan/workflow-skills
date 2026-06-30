---
title: /doctor migration WARN + new-shape validation
priority: medium
size: 2
status: new
created: 2026-06-30
source_branch: main
related_files:
  - commands/doctor.md # Check 1: add un-migrated WARN + projects shape validation
is_blocked_by: [linear_multi_project_task_2]
parent: linear_multi_project
tags: [linear, doctor, validation]
---

> Plan: [[linear_multi_project_plan]]

## Context

`/doctor` is the config-health check. Today it only checks the file parses as YAML — no
rigid Linear schema. With the hard cut to `projects:`, `/doctor` becomes the safety net
that tells a user their config is on the old scalar form (handlers will misbehave until
they migrate) and that a new-form config is well-shaped.

Blocked by Task 2 so the WARN can point at a working `/task-config` migration.

Design refs: spec §"Migration & doctor" → `/doctor` Check 1.

## Task

In `commands/doctor.md`, extend Check 1 with:

1. **Un-migrated WARN:** if a scalar `linear.default_project` is still present, WARN with
   the fix: "run `/task-config` to migrate to the `projects:` list".
2. **Shape validation** (when `projects` present):
   - `projects` is a list; each entry has an `id`.
   - **`id` values are unique** across the list → **ERROR** naming the offending id
     (duplicates double-count in-flight and fire redundant `list_issues` queries).
   - per-entry `wip_limit`/`max_estimate` are positive integers when present.
   - `linear.global_wip_limit`, when set, is a positive integer; **WARN** if it is `<` the
     largest per-project `wip_limit` (it would mask the per-project caps).

## Acceptance Criteria

- **Code-enforced:** `just check` passes. If `doctor.md` has companion fixtures/tests
  (`rg -l doctor scripts/ tests/ 2>/dev/null`), add cases for: scalar-present WARN,
  duplicate-id ERROR, non-positive `wip_limit` ERROR, low `global_wip_limit` WARN.
- **User-run:** run `/doctor` against (a) this repo's still-scalar config → see the
  migration WARN; (b) a hand-written config with two identical project ids → see the
  duplicate-id ERROR; (c) a clean two-project config → no Linear findings.
