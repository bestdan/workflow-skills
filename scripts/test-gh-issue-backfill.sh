#!/usr/bin/env bash
# test-gh-issue-backfill.sh — hermetic tests for gh-issue-backfill.py.
#
# Wraps scripts/test_gh_issue_backfill.py (stdlib unittest, no network: every
# run_gh seam is stubbed) so the prio: encoding, the hold rule and the
# rung-preservation guard are exercised by the same `run scripts/test-*.sh`
# gate as the other hermetic tests.
#
# Run directly: bash scripts/test-gh-issue-backfill.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_gh_issue_backfill.py"
