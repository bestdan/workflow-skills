#!/usr/bin/env bash
# test-task-config-resolve.sh — hermetic tests for task-config-resolve.py.
#
# Wraps scripts/test_task_config_resolve.py (stdlib unittest, no git: run_git is
# stubbed) so the three committed-layer sources — and the rule that a git
# failure is never read as "untracked" — are exercised by the same
# `run scripts/test-*.sh` gate as the other hermetic tests.
#
# Run directly: bash scripts/test-task-config-resolve.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_task_config_resolve.py"
