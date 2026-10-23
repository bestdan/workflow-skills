#!/usr/bin/env bash
# test-task-dashboard-overlay.sh — hermetic tests for the task-dashboard's
# overlay: pin, order, snooze and note writes, the export/import round trip and
# its validation, overlay rows surviving a reconcile that drops their issue, and
# the overlay POST routes behind the server's Host/Origin/token guard.
#
# Wraps scripts/test_task_dashboard_overlay.py (stdlib unittest), which reuses
# the server and sync suites' harnesses. Everything runs over temp DBs with fake
# acli runners. It runs under uv rather than bare python3 because the dashboard
# needs Python 3.11 and the python3 on a stock macOS box may be 3.9.
#
# Run directly: bash scripts/test-task-dashboard-overlay.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec uv run --no-project --python ">=3.11" python "$ROOT/scripts/test_task_dashboard_overlay.py"
