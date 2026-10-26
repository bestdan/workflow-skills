#!/usr/bin/env bash
# test-task-dashboard-fleet-tabs.sh — hermetic tests for the fleet instance's
# Pull requests and Issues tabs (scripts/task-dashboard/fleet_tabs.py): that
# each PR lies in one lane and a waiting one names its misses, that issues are
# laned by status label, that the issue cap is surfaced, and that Needs you
# and Stale still read only the human-review issues.
#
# Wraps scripts/test_task_dashboard_fleet_tabs.py (stdlib unittest). The
# fixture DB is written through github_fleet.write, and the server runs
# in-process with a fake sync function, so no gh or network runs. It runs
# under uv because the dashboard needs Python 3.11.
#
# Run directly: bash scripts/test-task-dashboard-fleet-tabs.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec uv run --no-project --python ">=3.11" python "$ROOT/scripts/test_task_dashboard_fleet_tabs.py"
