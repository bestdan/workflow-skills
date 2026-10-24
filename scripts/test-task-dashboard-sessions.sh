#!/usr/bin/env bash
# test-task-dashboard-sessions.sh — hermetic tests for the fleet view's session
# rows (scripts/task-dashboard/sessions.py and claude_remote.py).
#
# Wraps scripts/test_task_dashboard_sessions.py (stdlib unittest; every cloud
# read goes through a fake runner or dev/fake-claude, fed from
# scripts/task-dashboard/fixtures/claude/, so it never starts Claude Code).
# Runs under uv because the dashboard needs Python 3.11 and the python3 on a
# stock macOS box may be 3.9.
#
# Run directly: bash scripts/test-task-dashboard-sessions.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec uv run --no-project --python ">=3.11" python "$ROOT/scripts/test_task_dashboard_sessions.py"
