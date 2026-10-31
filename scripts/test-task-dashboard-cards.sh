#!/usr/bin/env bash
# test-task-dashboard-cards.sh — hermetic tests for the dashboard's one shared
# card (scripts/task-dashboard/cards.py): its layout, the bare age, the CI
# word, the score behind a "?", and a guard that no other module writes a
# card of its own.
#
# Wraps scripts/test_task_dashboard_cards.py (stdlib unittest). It renders
# cards in memory, so no gh, claude or network runs. It runs under uv because
# the dashboard needs Python 3.11.
#
# Run directly: bash scripts/test-task-dashboard-cards.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec uv run --no-project --python ">=3.11" python "$ROOT/scripts/test_task_dashboard_cards.py"
