#!/usr/bin/env bash
# test-gh-issue-trust.sh — hermetic tests for gh-issue-trust.py, the author gate.
#
# Wraps scripts/test_gh_issue_trust.py (stdlib unittest, run_gh stubbed).
#
# Run directly: bash scripts/test-gh-issue-trust.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_gh_issue_trust.py"
