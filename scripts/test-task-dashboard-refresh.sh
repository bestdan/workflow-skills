#!/usr/bin/env bash
# test-task-dashboard-refresh.sh — hermetic tests for the task-dashboard's
# per-item refresh: one PR or issue re-read and its rows rewritten, the card
# moving lane or leaving a page, a merged or closed item dropped from every
# view, POST /refresh's token and Origin checks, the card's script, and the
# CLI's `refresh` command.
#
# Wraps scripts/test_task_dashboard_refresh.py (stdlib unittest). Every gh call
# goes through a fake runner, and the server runs in process on an ephemeral
# 127.0.0.1 port over a temp DB, so no gh or network runs. It runs under uv
# rather than bare python3 because the dashboard needs Python 3.11.
#
# Run directly: bash scripts/test-task-dashboard-refresh.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec uv run --no-project --python ">=3.11" python "$ROOT/scripts/test_task_dashboard_refresh.py"
