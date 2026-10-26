#!/usr/bin/env bash
# test-task-dashboard-needs.sh — hermetic tests for the fleet view's Needs you
# page (scripts/task-dashboard/needs.py): the longest wait first and expanded,
# permission prompts batched under Approvals, a PR without a brief showing its
# header once with no brief block, and every card's title first and searchable.
#
# Wraps scripts/test_task_dashboard_needs.py (stdlib unittest). The fixture DB
# is written through the real writers, and the server runs in-process with a
# fake sync function, so no gh, git, claude or network runs. It runs under uv
# because the dashboard needs Python 3.11.
#
# Run directly: bash scripts/test-task-dashboard-needs.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec uv run --no-project --python ">=3.11" python "$ROOT/scripts/test_task_dashboard_needs.py"
