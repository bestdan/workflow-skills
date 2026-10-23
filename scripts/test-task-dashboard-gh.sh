#!/usr/bin/env bash
# test-task-dashboard-gh.sh — hermetic tests for the task-dashboard's GitHub
# adapter (scripts/task-dashboard/github_gh.py).
#
# Wraps scripts/test_task_dashboard_gh.py (stdlib unittest; every gh call goes
# through a fake runner fed from scripts/task-dashboard/fixtures/gh/, so it
# never reaches the network). Runs under uv because the dashboard needs Python
# 3.11 and the python3 on a stock macOS box may be 3.9.
#
# Run directly: bash scripts/test-task-dashboard-gh.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec uv run --no-project --python ">=3.11" python "$ROOT/scripts/test_task_dashboard_gh.py"
