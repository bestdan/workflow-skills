---
title: Schema + shared "resolve configured projects" helper
priority: high
size: 3
status: new
created: 2026-06-30
source_branch: main
related_files:
  - commands/handlers/linear-common.md # Config block (~L16-40); add projects + global_wip_limit; add the helper
  - commands/task-config.md # documented example schema (~L116)
is_blocked_by: []
parent: linear_multi_project
tags: [linear, config, schema]
tracker_id: PRE-330
tracker_url: https://linear.app/prethinkio/issue/PRE-330/schema-shared-resolve-configured-projects-helper
---

> Plan: [[linear_multi_project_plan]]

## Context

`linear-common.md` "## Config block" is the single source of truth for the Linear
handler's config schema; today it documents a scalar `linear.default_project`. Five+
consumers read that scalar directly (`linear-add`, `linear-list`, `linear-promote`,
`linear-claim`, `do-tasks`, plus `linear-archive`, `push-plan`). This task introduces the
new schema and a **shared resolution helper** so every consumer reads one normalized list
instead of re-implementing scalar handling. This is the keystone — all other tasks build
on the helper.

Design refs: spec §Schema, §"Shared resolution helper", §Rules.

## Task

In `commands/handlers/linear-common.md`:

1. Replace the `default_project` line in the Config block with the new shape:
   - `linear.projects`: a list of `{ id (required), name? , wip_limit?, max_estimate? }`.
   - `linear.global_wip_limit` (optional) — absolute ceiling on total in-flight across
     all configured projects. Lives **under `linear:`** (resolved planning decision — it's
     Linear-multi-project-specific, unlike the cross-handler top-level `wip_limit`).
   - Keep top-level `wip_limit` (shared w/ repo-pr & gh-issue) and `linear.max_estimate`
     as the inherited defaults. Document the inheritance and the absent/empty/one-entry
     rules verbatim from the spec §Rules.
2. Add a **"Resolve configured projects"** helper section: a documented step that returns
   `[{ id, name, wip_limit, max_estimate }]` with inheritance applied (per-entry override
   else global default). When `projects` is absent/empty, return a single synthetic
   whole-team scope (`id: null`, global `wip_limit`/`max_estimate`). Resolve missing
   `name` lazily via `list_projects`. This is the function every consumer calls.

In `commands/task-config.md`: update the documented example schema (~L116) from
`default_project: null` to the `projects:` list + `global_wip_limit` form so the generic
config reference matches.

Do **not** touch the consumers in this task — only the schema + helper definition. The
consumers are migrated in tasks 2–5.

## Acceptance Criteria

- **Code-enforced:** `just check` passes (dprint + `claude plugin validate --strict` +
  `scripts/validate.py`). No consumer file changed in this task's diff (`git diff --name-only`
  shows only `linear-common.md` and `task-config.md`).
- **User-run:** read the new "Resolve configured projects" section cold and confirm it is
  unambiguous about: (a) inheritance precedence, (b) the absent/empty → whole-team synthetic
  scope, (c) that `global_wip_limit` lives under `linear:` and is optional. A second reader should be
  able to implement a consumer against it without re-reading the spec.
