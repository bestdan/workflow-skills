---
type: epic
title: Linear multi-project config — implementation
status: active
owner: dp.egan@gmail.com
created: 2026-06-30
tracker_id: f51052a1-5148-43de-a8a4-6863a5653db4
tracker_url: https://linear.app/prethinkio/project/linear-multi-project-config-implementation-aa3ed05c7d67
---

# Linear multi-project config — implementation

Implements the design in
[`docs/superpowers/specs/2026-06-24-linear-multi-project-config-design.md`](../../../docs/superpowers/specs/2026-06-24-linear-multi-project-config-design.md)
(PR #97). Turns the Linear handler's single pinned `default_project` into a list of
configured `projects`, each with its own optional `wip_limit`/`max_estimate`, plus an
optional global WIP ceiling, a migration path off the scalar form, and `/doctor`
validation.

## Goal

Let a repo whose work spans several Linear projects scope every Linear-handled command
to those projects and cap WIP per-project (and optionally globally), instead of the
"one pinned project or the whole team" the scalar `default_project` allows today.

## Approach

A **keystone-first** slice. Task 1 lands the schema + a shared "resolve configured
projects" helper in `linear-common.md`; every other task reads that resolved list
instead of the raw scalar, so they fan out independently off Task 1. The cut over from
`default_project` is **hard** (per the design) — handlers read only `projects:` after
migration — so Task 2 (migration in `/task-config`) and Task 6 (`/doctor` WARN +
validation) are what keep a not-yet-migrated config from silently misbehaving.

Main tradeoff: the hard cut means a config still on scalar `default_project` breaks
until migrated. We mitigate with the `/task-config` auto-migration (Task 2) and a loud
`/doctor` WARN (Task 6) rather than a permanent dual-read compatibility shim — see Open
Questions for the alternative.

## Scope / non-goals

- **In:** `projects` list schema + `global_wip_limit`; resolution helper; per-project &
  global WIP; migration off scalar; `/doctor` validation; updating all 10 Linear-handler
  consumers (the refreshed "Files touched" table in the spec).
- **Out (per spec "Out of scope"):** per-project `base_branch`/`default_priority`/labels;
  independent per-project WIP for the repo-pr / gh-issue handlers (top-level `wip_limit`
  unchanged for them); a union default view for `/list-tasks` & `/promote-tasks` (`all`
  stays the explicit escape).

## Tasks

1. [[linear_multi_project_task_1]] — Schema + shared "resolve configured projects" helper
   (`linear-common.md`, `task-config.md` example). **Keystone — all others depend on it.**
2. [[linear_multi_project_task_2]] — `/task-config` multi-project setup + scalar→list
   migration (`linear-config.md`). Blocked by 1.
3. [[linear_multi_project_task_3]] — `/do-tasks` multi-project execution: candidate query
   across projects + per-project & global WIP gate (`linear-claim.md`, `do-tasks.md`).
   Blocked by 1.
4. [[linear_multi_project_task_4]] — Selection prompts: `/add-task`, `/list-tasks`,
   `/promote-tasks` scoping (`linear-add.md`, `linear-list.md`, `linear-promote.md`).
   Blocked by 1.
5. [[linear_multi_project_task_5]] — Remaining consumers: `/archive-tasks` multi-project
   sweep + `/push-plan` targeting (`linear-archive.md`, `push-plan.md`). Blocked by 1.
6. [[linear_multi_project_task_6]] — `/doctor` migration WARN + new-shape validation
   (`doctor.md`). Blocked by 2.

Dependency shape: `1 → {2, 3, 4, 5}`, and `2 → 6`. Tasks 3/4/5 can run in parallel once
1 lands; 2 and 6 are the migration spine.

## Resolved decisions (planning round, 2026-06-30)

1. **`global_wip_limit` placement** → **under `linear:`** (it's Linear-multi-project-specific;
   top-level stays for cross-handler keys). Diverges from the spec's original top-level
   placement — spec + Task 1/6 updated to match.
2. **`--project X` flag** → **deferred to a follow-up.** v1 ships the "all configured
   projects" behavior only. Tasks 3 & 5 no longer build the flag.
3. **Migration safety** → **hard cut** (per spec): handlers read only `projects:` after
   migration; a still-scalar config breaks until `/task-config` runs, backed by a loud
   `/doctor` WARN (Task 6). No dual-read compat shim.
4. **`/add-task` scope** → **configured projects only.** Once `projects` are configured,
   every task must go to one of them — no "team backlog" escape. Diverges from the spec's
   selection table — spec + Task 4 updated.

## Open questions (still open)

- **`/push-plan` across projects** — assumed: a whole plan pushes to **one** chosen
  project (prompt when multiple). Per-task project routing within a plan is **not** planned
  for v1. Flag if you want per-task routing.
