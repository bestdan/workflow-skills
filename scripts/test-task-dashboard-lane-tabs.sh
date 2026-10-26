#!/usr/bin/env bash
# test-task-dashboard-lane-tabs.sh — hermetic tests for the dashboard's lane
# tabs (scripts/task-dashboard/lane_tabs.py): one tab per lane with its count
# on every multi-lane page, one lane shown, `?lane=` picking it, a switched-off
# lane's "(off)", and the page script that switches tabs and follows the text
# filter.
#
# Wraps scripts/test_task_dashboard_lane_tabs.py (stdlib unittest). Fixture DBs
# are written through the real writers, and the server runs in-process with a
# fake sync function, so no acli, gh, git, claude or network runs. It runs
# under uv because the dashboard needs Python 3.11.
#
# Run directly: bash scripts/test-task-dashboard-lane-tabs.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec uv run --no-project --python ">=3.11" python "$ROOT/scripts/test_task_dashboard_lane_tabs.py"
