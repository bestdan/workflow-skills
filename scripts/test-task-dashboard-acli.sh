#!/usr/bin/env bash
# test-task-dashboard-acli.sh — hermetic tests for the task-dashboard's acli
# adapter (search, view fan-out and retry, field parsing, auth classification).
#
# Wraps scripts/test_task_dashboard_acli.py (stdlib unittest). No acli runs:
# a fake runner replays scripts/task-dashboard/fixtures/acli/. It runs under uv
# rather than bare python3 because the dashboard needs Python 3.11 and the
# python3 on a stock macOS box may be 3.9.
#
# Run directly: bash scripts/test-task-dashboard-acli.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec uv run --no-project --python ">=3.11" python "$ROOT/scripts/test_task_dashboard_acli.py"
