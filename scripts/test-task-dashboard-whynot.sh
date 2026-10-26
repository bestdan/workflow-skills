#!/usr/bin/env bash
# test-task-dashboard-whynot.sh — hermetic tests for the fleet Now's "why not
# now" picker (scripts/task-dashboard/whynot.py): the picker on a Now card,
# Skip still snoozing, each answer recorded with its score terms and
# features, the daily base rate, the Health section, and `dashboard.py tune`'s
# weight suggestions and candidate dimensions.
#
# Wraps scripts/test_task_dashboard_whynot.py (stdlib unittest). The fixture
# DB is written through the real writers, the server runs in-process with a
# fake sync function, and the CLI runs against a temp DB and config, so no
# gh, claude or network runs. It runs under uv because the dashboard needs
# Python 3.11.
#
# Run directly: bash scripts/test-task-dashboard-whynot.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec uv run --no-project --python ">=3.11" python "$ROOT/scripts/test_task_dashboard_whynot.py"
