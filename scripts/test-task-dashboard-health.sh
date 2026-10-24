#!/usr/bin/env bash
# test-task-dashboard-health.sh — hermetic tests for the task-dashboard's
# Health panel: each figure against hand-computed fixture answers, the
# "baseline" label on the waits, the targets in config, and `/health` agreeing
# with `dashboard.py health --json` over the same window.
#
# Wraps scripts/test_task_dashboard_health.py (stdlib unittest). The server runs
# in-process on an ephemeral 127.0.0.1 port over a temp DB, with a fake sync
# function, so no acli or gh runs. It runs under uv rather than bare python3
# because the dashboard needs Python 3.11 and the python3 on a stock macOS box
# may be 3.9.
#
# Run directly: bash scripts/test-task-dashboard-health.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec uv run --no-project --python ">=3.11" python "$ROOT/scripts/test_task_dashboard_health.py"
