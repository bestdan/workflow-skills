#!/usr/bin/env bash
# test-task-dashboard-plans.sh — hermetic tests for `dashboard.py plan
# set|show|clear` (scripts/task-dashboard/plans.py): a plan round-trips in
# order, belongs to one scope or `all`, a session plan expires at the next 04:00
# local, an entry outside the mirror is refused with its key, and export then
# import keeps plans. Also Now's Plan mode (plan_mode.py): the live plan in
# its order with reasons, on the page and in `view now --mode plan --json`, a
# done item ticked, one that left the mirror marked gone, and the empty state.
# And plan_ticks.py: the sync's plans step re-reading gone GitHub entries
# through a fake gh, a session plan's outcome record, and a "why not now"
# answer carrying the plan's reason.
#
# Wraps scripts/test_task_dashboard_plans.py (stdlib unittest). It reuses the
# agent-query suite's fixture mirror, written through the real writers into a
# temp-file DB, so no acli, gh, git or network runs and the live DB is never
# opened. It runs under uv because the dashboard needs Python 3.11.
#
# Run directly: bash scripts/test-task-dashboard-plans.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec uv run --no-project --python ">=3.11" python "$ROOT/scripts/test_task_dashboard_plans.py"
