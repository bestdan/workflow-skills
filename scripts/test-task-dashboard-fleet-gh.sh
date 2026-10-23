#!/usr/bin/env bash
# test-task-dashboard-fleet-gh.sh — hermetic tests for the fleet view's GitHub
# reads (scripts/task-dashboard/github_fleet.py).
#
# Wraps scripts/test_task_dashboard_fleet_gh.py (stdlib unittest; every gh call
# goes through a fake runner fed from scripts/task-dashboard/fixtures/gh/fleet_*,
# so it never reaches the network). Runs under uv because the dashboard needs
# Python 3.11 and the python3 on a stock macOS box may be 3.9.
#
# Run directly: bash scripts/test-task-dashboard-fleet-gh.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec uv run --no-project --python ">=3.11" python "$ROOT/scripts/test_task_dashboard_fleet_gh.py"
