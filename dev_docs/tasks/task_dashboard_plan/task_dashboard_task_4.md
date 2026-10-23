---
title: task-dashboard — sync engine (scopes, JQL, incremental/reconcile/full, transactions)
priority: high
size: 5
status: new
created: 2026-10-23
source_branch: bestdan/task-dashboard-design
related_files:
  - dev_docs/designs/2026-10-23-task-dashboard.md
  - scripts/task-dashboard/jira_acli.py
  - scripts/task-dashboard/github_gh.py
  - scripts/task-dashboard/db.py
is_blocked_by:
  - task_dashboard_task_2
  - task_dashboard_task_3
parent: task_dashboard
tags: [task-dashboard, sync]
---

Part of [[task_dashboard_plan]].

## Context

The sync turns adapter output (tasks 2 and 3) into mirror rows. The design's
**Sync** section is the spec: scopes, the done window, the relative
incremental cursor, the reconcile that both adds and drops, and **Failure**
(fetch first, write once). The rules that are easy to get wrong:

- **Wrap every scope in parentheses before appending clauses.** JQL binds
  `AND` tighter than `OR`, and the reported/watched scope contains an `OR`.
- **The incremental cursor is relative:** `updated >= "-<N>m"`, where N is the
  minutes since the last _successful_ run _started_, plus 5. Absolute JQL dates
  are read in the Jira profile timezone (the spike measured a 4-hour skew), and
  `search` can't return `updated` to give a high-water mark.
- **The sprint scope is `sprint in (<ids>)`**, using the active sprints of the
  configured `board_ids`. Don't use `openSprints()`: it covers every team's
  sprints in a shared project.
- **The linked scope is a second pass:** blocker and blocked keys from the
  first pass's `links`, fetched only if not already in the union.
- **A run fetches everything, then writes all mirror changes in one SQLite
  transaction.** The `sync_runs` row is written outside that transaction, so a
  failure is still recorded.

## Task

- Add `scripts/task-dashboard/sync.py`:
  - `build_scopes(config, sprint_ids) -> list[str]` returns each scope as a
    parenthesized JQL string with the done window applied:
    `AND (statusCategory != Done OR resolved >= -<done_window_days>d)`.
  - `run(mode, config, db, now)` for `mode` in `incremental | reconcile | full`:
    - `incremental`: search the union with the cursor appended, then view only
      the returned keys.
    - `reconcile`: search keys only (no cursor), view the keys missing from the
      mirror, and drop mirror rows whose key no scope returned.
    - `full`: view every key.
    - Every mode then runs the linked pass and the GitHub adapter (when
      enabled), and passes the project keys present in `issues` as the
      PR-join allowlist.
  - Record each run in `sync_runs`: mode, start, end, outcome
    (`ok | failed | auth_failed`), counts, error text and failed keys.
    `AuthError` from the adapter becomes `auth_failed`. Keys that failed
    `view` twice are logged, not fatal.
  - With no successful run recorded yet, `incremental` falls back to `full`.
- Add a `sync [--mode …]` subcommand to `dashboard.py`, run as
  `uv run --no-project --python ">=3.11" scripts/task-dashboard/dashboard.py
  sync`, so a sync can be run by hand before the server exists.
- Add `scripts/test_task_dashboard_sync.py` and its `.sh` wrapper, driving
  fake adapters.

## Acceptance Criteria

**Code-enforced**

- Every string `build_scopes` returns starts with `(`, and the reported/watched
  scope's `OR` sits inside the parentheses.
- The incremental cursor for a last successful start 17 minutes ago is
  `-22m`. A failed run does not advance it.
- Reconcile, with the fake returning a new key and omitting an old one, adds
  the new key and deletes the old key's `issues` and `links` rows. Overlay rows
  for the old key survive.
- An adapter raising mid-run leaves `issues` byte-identical (compare a
  checksum) and writes a `failed` `sync_runs` row. `AuthError` writes
  `auth_failed`.
- `just check` passes.

**User-run**

- On the work laptop, `dashboard.py sync --mode full` completes in about two
  minutes or less against the real union. A second `--mode incremental` run
  right after takes seconds and views a handful of keys at most.
