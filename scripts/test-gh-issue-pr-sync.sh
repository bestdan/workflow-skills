#!/usr/bin/env bash
# test-gh-issue-pr-sync.sh — hermetic tests for gh-issue-pr-sync.py.
#
# Wraps scripts/test_gh_issue_pr_sync.py (stdlib unittest, no network: the
# gh-issue-state run_gh seam is stubbed) so the PR-driven status transitions,
# their no-op gates, and the workflow's trigger list are exercised by the same
# `run scripts/test-*.sh` gate as the other hermetic tests.
#
# Run directly: bash scripts/test-gh-issue-pr-sync.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_gh_issue_pr_sync.py"
