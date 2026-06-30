---
title: /task-config multi-project setup + scalar→list migration
priority: high
size: 3
status: new
created: 2026-06-30
source_branch: main
related_files:
  - commands/handlers/linear-config.md # /task-config Linear setup; owns project prompt + id resolution (4 default_project refs)
is_blocked_by: [linear_multi_project_task_1]
parent: linear_multi_project
tags: [linear, config, migration]
---

> Plan: [[linear_multi_project_plan]]

## Context

`linear-config.md` owns the `/task-config` Linear setup: the team/project/priority
prompts and the rule "resolve `default_project` against real projects — do not accept
free-text" (steps ~3, ~42, ~52). It currently writes a single `default_project`. This
task makes setup write the `projects:` list and migrate an existing scalar config.

The migration is a **hard cut** (spec §Migration): after conversion, handlers read only
`projects:`. This very repo's `dev_docs/tasks/.task-config.yml` is still on the scalar
form (`default_project: ebbc284b-…`), so it's the natural first migration test.

Depends on Task 1 for the target schema shape.

## Task

In `commands/handlers/linear-config.md`:

1. **Multi-project setup (fresh):** loop the existing single-project resolution prompt so
   the user can add **one or more** projects, each resolved against `list_projects` (keep
   the no-free-text rule). Offer optional per-project `wip_limit`/`max_estimate` overrides,
   and the optional top-level `global_wip_limit`. Write them as the `projects:` list.
   Preserve the "None — prompt me per-task" path (writes empty/absent `projects`).
2. **Migration (existing scalar):** detect a scalar `linear.default_project`, resolve its
   name via `list_projects`, and rewrite it as a **one-entry** `projects:` list with no
   overrides (inherits the globals). After writing, the scalar key is removed (hard cut).
3. Update the file's own example/written-config snippets (~L52) to the new shape.

Keep the prompts terse; this is a setup wizard, not a form.

## Acceptance Criteria

- **Code-enforced:** `just check` passes. The file no longer writes a bare
  `default_project` key (`rg 'default_project:' commands/handlers/linear-config.md` only
  appears in migration-detection context, not as a written output key).
- **User-run:** run `/task-config` against this repo's existing scalar config and confirm
  it rewrites `default_project: ebbc284b-…` into a one-entry `projects:` list with the
  resolved name, and removes the scalar key. Then run a fresh setup and add two projects;
  confirm both land with correct (inherited vs overridden) limits.
