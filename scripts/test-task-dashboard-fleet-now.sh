#!/usr/bin/env bash
# test-task-dashboard-fleet-now.sh — hermetic tests for the fleet instance's
# Now (scripts/task-dashboard/fleet_now.py) and its score (score.fleet_score):
# the default category order, pins first, each card's why line adding up to
# its score, the [score.fleet] weights' defaults and validation, and `/`
# serving Now on a fleet instance.
#
# Wraps scripts/test_task_dashboard_fleet_now.py (stdlib unittest). The
# fixture DB is written through the real writers, and the server runs
# in-process with a fake sync function, so no gh, git, claude or network
# runs. It runs under uv because the dashboard needs Python 3.11.
#
# Run directly: bash scripts/test-task-dashboard-fleet-now.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec uv run --no-project --python ">=3.11" python "$ROOT/scripts/test_task_dashboard_fleet_now.py"
