#!/usr/bin/env bash
# test-gh-issue-rollups.sh — hermetic tests for gh-issue-rollups.py's parent-rollup lookup.
#
# Wraps scripts/test_gh_issue_rollups.py (stdlib unittest, no network: run_gh is
# stubbed) so the pagination, shape validation and fail-closed guards are
# exercised by the same `run scripts/test-*.sh` gate as the other hermetic tests.
#
# Run directly: bash scripts/test-gh-issue-rollups.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_gh_issue_rollups.py"
