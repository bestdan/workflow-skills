#!/usr/bin/env bash
# test-task-dashboard-sync.sh — hermetic tests for the task-dashboard's sync
# engine (scopes, cursor, incremental/reconcile/full, the one-transaction
# write, sync_runs) and for the fake acli under dev/.
#
# Wraps scripts/test_task_dashboard_sync.py (stdlib unittest). No acli or gh
# runs: fake runners stand in for both, and the end-to-end cases point
# jira.acli_path at scripts/task-dashboard/dev/fake-acli. It runs under uv
# rather than bare python3 because the dashboard needs Python 3.11 and the
# python3 on a stock macOS box may be 3.9.
#
# Run directly: bash scripts/test-task-dashboard-sync.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec uv run --no-project --python ">=3.11" python "$ROOT/scripts/test_task_dashboard_sync.py"
