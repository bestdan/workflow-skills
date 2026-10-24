#!/usr/bin/env bash
# test-task-dashboard-fleet-views.sh — hermetic tests for the fleet view's
# Since you were away and Stale pages (scripts/task-dashboard/fleet_views.py):
# what changed since the overlay's last visit, that loading the page records no
# visit while "Mark as read" does, and the close-by-evidence lanes of Stale.
#
# Wraps scripts/test_task_dashboard_fleet_views.py (stdlib unittest). The
# fixture DB is written through github_fleet.write and snapshot_adapter.ingest,
# and the server runs in-process with a fake sync function, so no gh, git or
# network runs. It runs under uv because the dashboard needs Python 3.11.
#
# Run directly: bash scripts/test-task-dashboard-fleet-views.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec uv run --no-project --python ">=3.11" python "$ROOT/scripts/test_task_dashboard_fleet_views.py"
