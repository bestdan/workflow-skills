#!/usr/bin/env bash
# test-task-dashboard-agent-view.sh — hermetic tests for `dashboard.py view`
# and `dashboard.py overlay` (scripts/task-dashboard/agent_view.py): a view's
# JSON lists the same lanes, items and counts its page renders, carries no
# HTML, marks what --all adds, and an overlay write from the CLI goes through
# the server's own validation and hides the item everywhere.
#
# Wraps scripts/test_task_dashboard_agent_view.py (stdlib unittest). Fixture
# DBs are written through the real writers, and the one server test runs
# in-process with a fake sync function, so no acli, gh, git or network runs.
# It runs under uv because the dashboard needs Python 3.11.
#
# Run directly: bash scripts/test-task-dashboard-agent-view.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec uv run --no-project --python ">=3.11" python "$ROOT/scripts/test_task_dashboard_agent_view.py"
