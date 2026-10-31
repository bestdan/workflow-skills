#!/usr/bin/env bash
# test-task-dashboard-linear-now.sh — hermetic tests for Linear issues as a
# source of the task-dashboard's Now: the assigned-issues read
# (linear_api.py), its sync step (linear_issues.py) and its candidates
# (fleet_now.py).
#
# Wraps scripts/test_task_dashboard_linear_now.py (stdlib unittest). Nothing
# reaches Linear: a fake runner answers the GraphQL query. It runs under uv
# rather than bare python3 because the dashboard needs Python 3.11 and the
# python3 on a stock macOS box may be 3.9.
#
# Run directly: bash scripts/test-task-dashboard-linear-now.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec uv run --no-project --python ">=3.11" python "$ROOT/scripts/test_task_dashboard_linear_now.py"
