#!/usr/bin/env bash
# test-task-dashboard-agent-remote.sh — hermetic tests for running the agent
# commands on another machine's dashboard: the `POST /agent` route and its
# argv checks, the `[remote]` client, the `[agent]` config and
# `dashboard.py agent-token add`.
#
# Wraps scripts/test_task_dashboard_agent_remote.py (stdlib unittest). It runs
# under uv because the dashboard needs Python 3.11. The server binds an
# ephemeral 127.0.0.1 port and nothing leaves the machine.
#
# Run directly: bash scripts/test-task-dashboard-agent-remote.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec uv run --no-project --python ">=3.11" python "$ROOT/scripts/test_task_dashboard_agent_remote.py"
