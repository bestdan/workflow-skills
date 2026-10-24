#!/usr/bin/env bash
# test-task-dashboard-collector.sh — hermetic tests for the fleet view's
# collector: `dashboard.py collect` against a fake ingest server (cursor,
# spool, retry), the `[collector]` config, the stable launcher's installPath
# resolution, and `install --collector-only`'s launchd and systemd units
# against golden files, with every scheduler command faked.
#
# Wraps scripts/test_task_dashboard_collector.py (stdlib unittest). It runs
# under uv because the dashboard needs Python 3.11. Nothing is loaded into
# launchd or systemd and nothing leaves 127.0.0.1.
#
# Run directly: bash scripts/test-task-dashboard-collector.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec uv run --no-project --python ">=3.11" python "$ROOT/scripts/test_task_dashboard_collector.py"
