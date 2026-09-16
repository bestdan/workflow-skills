#!/usr/bin/env bash
# test-gh-issue-reconcile.sh — hermetic tests for gh-issue-reconcile.py's label invariants.
#
# Wraps scripts/test_gh_issue_reconcile.py (stdlib unittest, no network: run_gh
# is stubbed in both the reconciler and gh-issue-state.py) so the three rules
# and their dry-run posture are exercised by the same `run scripts/test-*.sh`
# gate as the other hermetic tests.
#
# Run directly: bash scripts/test-gh-issue-reconcile.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_gh_issue_reconcile.py"
