---
title: task-dashboard — package skeleton, config, SQLite schema, test wiring
priority: high
size: 3
status: new
created: 2026-10-23
source_branch: bestdan/task-dashboard-design
related_files:
  - dev_docs/designs/2026-10-23-task-dashboard.md
  - scripts/typecheck.sh
  - scripts/tier-coverage.py
  - scripts/check.sh
  - scripts/local-review/server.py
is_blocked_by:
parent: task_dashboard
tags: [task-dashboard, scaffolding]
---

Part of [[task_dashboard_plan]].

## Context

The dashboard is a single stdlib-only Python process under
`scripts/task-dashboard/`, run from a `launchd` agent. The design is
`dev_docs/designs/2026-10-23-task-dashboard.md`; read **Architecture**,
**Mirror vs. overlay** and **Config**.

**Python 3.11+, run through uv.** The dashboard runs under
`uv run --no-project --python ">=3.11"` rather than macOS's bundled `python3`,
which is 3.9. So `tomllib` is available, and the config is TOML. It still uses
no third-party packages.

That interpreter choice affects type-checking. `scripts/typecheck.sh` assigns
every `.py` file to a tier by the interpreter it must survive, and
`scripts/tier-coverage.py` fails the gate on any file no tier claims. Read the
header and `dev_docs/decisions/2026-09-04-python-type-checking.md`. The
existing `consumer` tier pins 3.9 and is wrong for this code. The `dev` tier
pins 3.11 but is described as contributor-only. Either add a `uv-runtime` tier
pinned to 3.11, or widen the `dev` tier's description to cover uv-run runtime
code. State the choice in the typecheck header.

Repo test convention: `scripts/test_<subject>.py` (stdlib `unittest`, hermetic)
wrapped by `scripts/test-<subject>.sh`, which `scripts/check.sh` runs. See
`scripts/test_diff_anchor_check.py` and `scripts/test-body-refs.sh` for the
pattern, including loading a module by file path. The existing wrappers
`exec python3`. These must run under 3.11+ too
(`uv run --no-project --python ">=3.11"`), or CI and the laptop may test on an
interpreter the dashboard doesn't support. `uv` is already in CI
(`.github/workflows/ci.yml`).

## Task

- Create `scripts/task-dashboard/` with importable module files:
  - `config.py`: load and validate `~/.config/task-dashboard/config.toml`
    with `tomllib`.
    Fill defaults from the design's example (`interval_minutes` 5,
    `reconcile_minutes` 60, `done_window_days` 14, `view_concurrency` 8, port
    8765, the score weights). On a missing required key (`jira.site`,
    `jira.board_ids`, `jira.fields.sprint`, `jira.fields.flagged`), raise an
    error that names the key. Allow overriding the path through an env var or
    argument, for tests.
  - `db.py`: open `~/.local/state/task-dashboard/dashboard.db`, creating the
    directory, with WAL on. Create these tables:
    - mirror: `issues`, `links`, `sprints`, `prs`, `sync_runs`
    - overlay: `pins`, `order`, `snoozes`, `notes`, keyed by issue key or PR URL
    - `meta`, holding a schema version

    Add a tiny migration hook (run the DDL for version N if the stored version
    is lower), so later tasks can add columns. Give `issues` columns for every
    field in the design's **Fields** list: `status_name`, `status_category`,
    `status_category_changed` and so on, plus a `raw_json` column for anything
    unanticipated.
- Wire the new files into a 3.11-pinned tier in `scripts/typecheck.sh`, per
  the choice above.
- Add `scripts/test_task_dashboard_db.py`, with a `.sh` wrapper that runs it
  through uv, to `scripts/check.sh`.
- Add `dashboard.py` as the CLI entry point, with subcommands added by later
  tasks. If `sys.version_info < (3, 11)`, it exits with a clear message.

## Acceptance Criteria

**Code-enforced**

- `scripts/test_task_dashboard_db.py`:
  - a fresh DB gets every table, and WAL mode is on
  - opening twice is idempotent
  - a config missing `jira.board_ids` raises an error naming that key
  - defaults fill unspecified keys
- `bash scripts/typecheck.sh` passes with the new files in a 3.11-pinned tier,
  and `scripts/tier-coverage.py` is green.
- Running `dashboard.py` under Python 3.9 prints the version requirement and
  exits non-zero.
- `just check` passes.

**User-run**

- None. This task has no live surface.
