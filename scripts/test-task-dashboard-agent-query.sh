#!/usr/bin/env bash
# test-task-dashboard-agent-query.sh — hermetic tests for `dashboard.py scopes`
# and `dashboard.py items` (scripts/task-dashboard/agent_query.py): what a
# scope word resolves to, the reserved `all`, the `related` workstreams of a
# repo scope, each item's joins (PR, activity, workstreams, overlay, Now
# terms), the size buckets and `[size.minutes]`, and that a read neither
# writes the DB nor prints the server token.
#
# Wraps scripts/test_task_dashboard_agent_query.py (stdlib unittest). The
# fixture DB is written through the real writers, so no acli, gh, git or
# network runs. It runs under uv because the dashboard needs Python 3.11.
#
# Run directly: bash scripts/test-task-dashboard-agent-query.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec uv run --no-project --python ">=3.11" python "$ROOT/scripts/test_task_dashboard_agent_query.py"
