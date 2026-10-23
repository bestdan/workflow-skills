#!/usr/bin/env bash
# test-task-dashboard-ingest.sh — hermetic tests for the fleet view's ingest:
# POST /ingest's token, size, schema and Host checks, the snapshot adapter's
# per-host replace and delta append, the [ingest] config and the
# `dashboard.py ingest-token add` command.
#
# Wraps scripts/test_task_dashboard_ingest.py (stdlib unittest). The server runs
# in-process on an ephemeral 127.0.0.1 port over a temp DB, with synthetic
# snapshots only. It runs under uv because the dashboard needs Python 3.11.
#
# Run directly: bash scripts/test-task-dashboard-ingest.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec uv run --no-project --python ">=3.11" python "$ROOT/scripts/test_task_dashboard_ingest.py"
