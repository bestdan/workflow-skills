#!/usr/bin/env bash
# test-task-dashboard-brief.sh — hermetic tests for `dashboard.py brief`
# (scripts/task-dashboard/brief.py): every section within the token budget,
# the PR readiness rule, a Now mode's candidates in its order, `--minutes`
# keeping the est_min sum within twice the box with the unsized apart, Deep's
# inlined dossier, the gaps, which list gives way first past the budget, and
# that a brief neither writes the DB nor prints the server token.
#
# Wraps scripts/test_task_dashboard_brief.py (stdlib unittest). It reuses the
# agent-query suite's fixture mirror, written through the real writers into a
# temp-file DB, so no acli, gh, git or network runs and the live DB is never
# opened. It runs under uv because the dashboard needs Python 3.11.
#
# Run directly: bash scripts/test-task-dashboard-brief.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec uv run --no-project --python ">=3.11" python "$ROOT/scripts/test_task_dashboard_brief.py"
