#!/usr/bin/env bash
# test-task-dashboard-include.sh — hermetic tests for the dashboard's per-view
# include filters (scripts/task-dashboard/include.py): every view honours
# ?include=, an unknown group is a 400, a remembered choice survives a restart
# and loses to an explicit URL, config defaults validate, and the header gauges
# never change with a filter.
#
# Wraps scripts/test_task_dashboard_include.py (stdlib unittest). Fixture DBs
# are written through the real writers, and the server runs in-process with a
# fake sync function, so no acli, gh, git, claude or network runs. It runs
# under uv because the dashboard needs Python 3.11.
#
# Run directly: bash scripts/test-task-dashboard-include.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec uv run --no-project --python ">=3.11" python "$ROOT/scripts/test_task_dashboard_include.py"
