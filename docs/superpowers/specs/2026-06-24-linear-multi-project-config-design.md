# Linear multi-project config — design

**Date:** 2026-06-24
**Status:** Approved (brainstorm) — ready for implementation plan

## Problem

The Linear task handler config (`dev_docs/tasks/.task-config.yml`) supports **one
team and at most one pinned project** (`linear.default_project`, a single UUID) plus a
single top-level `wip_limit`. A repo whose work spans multiple Linear projects cannot:

- scope `/do-tasks`, `/add-task`, `/list-tasks`, `/promote-tasks` to several specific
  projects (only "one project" or "the whole team"); and
- give each project its own WIP ceiling — `wip_limit` is one global number.

`default_project` is a flat scalar read by five consumers (`linear-add.md`,
`linear-list.md`, `linear-promote.md`, `linear-claim.md`, `do-tasks.md`); nothing
consumes a list. There is no rigid schema — `/doctor` only checks the file parses as
YAML — so the gap is in the handler instructions, not validation.

## Goals

- Configure **multiple projects** under one team, each with its own optional
  `wip_limit` and `max_estimate`.
- `/do-tasks` works across all configured projects, each enforcing its own WIP cap.
- Provide a migration path from the scalar `default_project` form, then hard-cut.

Backwards compatibility beyond the migration path is **not** a goal: after migration,
handlers read only the new format.

## Schema

```yaml
handler: linear
wip_limit: 3 # top-level global default (still shared w/ repo-pr & gh-issue handlers)
global_wip_limit: 6 # optional — absolute ceiling on TOTAL in-flight across all projects
# (absent → no global ceiling; the sum of per-project caps applies)
linear:
  team: PreThink
  default_priority: 3 # global
  base_branch: main # global
  max_estimate: 3 # global default
  projects: # NEW — replaces scalar default_project
    - id: ebbc284b-…
      name: Handler parity follow-ups # optional, for prompts/reports
      wip_limit: 5 # per-project override (else inherits top-level wip_limit)
      max_estimate: 5 # per-project override (else inherits linear.max_estimate)
    - id: 9f3a-…
      name: Payments revamp
      # no overrides → inherits wip_limit 3, max_estimate 3
```

Rules:

- `projects` **absent or empty** → whole-team scope with the single global `wip_limit`
  (preserves today's "no pin" behavior).
- `projects` with **exactly one** entry → equivalent to today's single pin.
- `wip_limit` stays **top-level** so the repo-pr and gh-issue handlers are untouched;
  per-project entries override it for Linear only. `max_estimate` stays under `linear`
  as the inherited default.
- Per-project keys: **only** `wip_limit` and `max_estimate`. `team`, `base_branch`,
  `default_priority` remain global.
- `id` is required per entry; `name` is optional (used for prompts/reports; resolved
  via `list_projects` when absent).

### Shared resolution helper (linear-common.md)

A single "resolve configured projects" step returns a list of
`{id, name, wip_limit, max_estimate}` with inheritance already applied (per-entry
override else the global default). Every consumer reads this instead of the raw scalar.
When `projects` is absent/empty it returns a single synthetic "whole team" scope
(`id: null`, global `wip_limit`/`max_estimate`).

## Selection semantics per command

| Command                          | Behavior with multiple configured projects                                                                                                                                            |
| -------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/do-tasks --all` (unscoped)     | Pulls across **all** configured projects; each enforces its **own** `wip_limit` independently. `--project X` narrows to one.                                                          |
| `/do-tasks` (single, foreground) | Ranks candidates across all configured projects; before claiming the chosen one, checks **that project's** WIP slack, skips to the next candidate (possibly another project) if full. |
| `/add-task`                      | Prompts among the **configured** projects (+ "No project (team backlog)"). Skips the prompt when only one project is configured.                                                      |
| `/list-tasks`                    | Prompts to pick one; `/list-tasks all` shows the union, grouped/labeled by project.                                                                                                   |
| `/promote-tasks`                 | Prompts to pick one configured project; `/promote-tasks all` scores all configured backlogs.                                                                                          |

`all` means **all configured projects**, not the whole team backlog — the config now
enumerates the projects of interest.

## Per-project WIP enforcement

The in-flight count — computed once for the pin today — becomes **per-project**:

- **Counting:** for each configured project, call `list_issues` with `teamId` + that
  project's `projectId`, count issues in any `started`-type state. That project's slack
  = `project.wip_limit − project.in_flight`.
- **Batch `/do-tasks --all`:** dispatch up to each project's own slack, independently.
  Effective batch = `Σ max(0, slack_p)` across projects, with no project exceeding its
  own cap. (E.g. dispatch 2 from project A with slack 2, 0 from a full project B in the
  same run.) The held-overflow report notes which project each held task belongs to.
- **Single `/do-tasks`:** rank candidates across all configured projects; the pre-claim
  gate checks the **chosen candidate's** project slack. If that project is full, skip to
  the next candidate (which may live in another project) rather than declining outright.
- **Global ceiling (optional `global_wip_limit`).** Per-project caps alone let total
  concurrent work scale to `Σ project.wip_limit` — e.g. three projects each inheriting
  the default `wip_limit: 3` permit **9** in-flight, defeating the point of a global
  cap. When `global_wip_limit` is set, it is an **absolute upper bound on total
  in-flight across all configured projects**, enforced _in addition to_ the per-project
  caps: a claim proceeds only if **both** the chosen candidate's project has slack
  **and** `Σ in_flight` across all projects `< global_wip_limit`. For `--all`, the batch
  is additionally clamped so the run never pushes the global total past the ceiling
  (dispatch `min(per-project slack, remaining global slack)` as projects are filled in
  rank order). Absent → no global ceiling; the sum of per-project caps applies (today's
  behavior). Its decline message: `Global WIP limit <N> reached (<count> in flight across all projects) — no issue claimed`.
- **Decline message** gains the project name:
  `WIP limit 5 reached (5 in flight) in project <name> — no issue claimed`.

## Migration & doctor

- **`/task-config`** detects a scalar `linear.default_project`, resolves its name via
  `list_projects`, and rewrites it as a one-entry `projects:` list. Fresh setup lets the
  user add one or more projects. After conversion it is a **hard cut** — handlers read
  only `projects:`.
- **`/doctor`** Check 1 gains:
  1. **Un-migrated WARN** — if scalar `default_project` is still present, warn with the
     fix ("run `/task-config` to migrate").
  2. **Shape validation** — `projects` is a list; each entry has an `id`; the `id`
     values are **unique** across the list (duplicates would double-count in-flight
     issues and fire redundant `list_issues` queries during WIP enforcement — ERROR
     with the offending id); per-entry `wip_limit`/`max_estimate` are positive integers
     when present; `global_wip_limit`, when set, is a positive integer (and ideally
     `≥` the largest per-project `wip_limit`, else it would mask them — WARN if not).

## Files touched

| File                                                 | Change                                                                                                                   |
| ---------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `commands/handlers/linear-common.md`                 | New schema; shared "resolve configured projects" helper (list of `{id, name, wip_limit, max_estimate}` with inheritance) |
| `commands/handlers/linear-claim.md`                  | Find candidates across configured projects; per-project WIP in claim                                                     |
| `commands/do-tasks.md`                               | Pre-claim WIP gate + batch slack computed per-project                                                                    |
| `commands/handlers/linear-add.md`                    | Prompt among configured projects (skip if one)                                                                           |
| `commands/handlers/linear-list.md`                   | Pick-one / `all` union                                                                                                   |
| `commands/handlers/linear-promote.md`                | Scope among configured projects; `all` = all configured                                                                  |
| `skills/task-config/*` (+ any Linear config handler) | Migration + multi-project setup                                                                                          |
| `commands/doctor.md`                                 | Migration WARN + new-shape validation                                                                                    |

## Out of scope

- Per-project `base_branch`, `default_priority`, or labels.
- Independent per-project WIP for the repo-pr / gh-issue handlers (top-level `wip_limit`
  unchanged for them).
- A union default view for `/list-tasks` / `/promote-tasks` (both prompt; `all` is the
  explicit union escape).
