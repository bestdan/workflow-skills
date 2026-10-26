#!/usr/bin/env bash
# test-task-dashboard-workstreams.sh — hermetic tests for the fleet instance's
# Workstreams tab (scripts/task-dashboard/workstreams.py) and its Linear reads
# (linear_api.py): that a milestone, a Linear project and a proposed design
# each land in the right lane with the right progress and members, that the
# Linear key lookup is one batched query, that Linear off or keyless makes no
# call, that `?ws=` filters Pull requests and Issues, that an idle workstream
# is Stalled, and that `view workstreams --json` matches the page.
#
# Wraps scripts/test_task_dashboard_workstreams.py (stdlib unittest). Every gh
# and Linear call goes to a fake runner, so no network runs. It runs under uv
# because the dashboard needs Python 3.11.
#
# Run directly: bash scripts/test-task-dashboard-workstreams.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec uv run --no-project --python ">=3.11" python "$ROOT/scripts/test_task_dashboard_workstreams.py"
