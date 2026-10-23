#!/usr/bin/env bash
# test-task-dashboard-snapshot.sh — hermetic tests for the fleet view's machine
# snapshot (scripts/task-dashboard/snapshot.py).
#
# Wraps scripts/test_task_dashboard_snapshot.py (stdlib unittest over a
# synthetic fixture built per test in a temp directory; git runs for real with
# its global config pinned to /dev/null, tmux and branch-remove.sh are faked).
# Runs under uv because the dashboard needs Python 3.11 and the python3 on a
# stock macOS box may be 3.9.
#
# Run directly: bash scripts/test-task-dashboard-snapshot.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec uv run --no-project --python ">=3.11" python "$ROOT/scripts/test_task_dashboard_snapshot.py"
