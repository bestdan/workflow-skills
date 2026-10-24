#!/usr/bin/env bash
# test-task-dashboard-install.sh — hermetic tests for the dashboard's
# LaunchAgent install, its stable launcher, status and sync-now
# (scripts/task-dashboard/server_install.py, task-dashboard-launch.sh).
#
# Wraps scripts/test_task_dashboard_install.py (stdlib unittest). launchctl is
# never run, every path is under a temp HOME, and the launcher cases run the
# rendered launcher for real under uv against fixture installed_plugins.json
# files. It runs under uv rather than bare python3 because the dashboard needs
# Python 3.11 (tomllib) and the python3 on a stock macOS box may be 3.9.
#
# Run directly: bash scripts/test-task-dashboard-install.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec uv run --no-project --python ">=3.11" python "$ROOT/scripts/test_task_dashboard_install.py"
