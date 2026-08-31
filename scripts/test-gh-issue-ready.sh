#!/usr/bin/env bash
# test-gh-issue-ready.sh — hermetic tests for gh-issue-ready.py's dependency readiness.
#
# Wraps scripts/test_gh_issue_ready.py (stdlib unittest, no network: run_gh is
# stubbed) so the readiness rule and read-only guarantee are exercised by the
# same `run scripts/test-*.sh` gate as the other hermetic tests.
#
# Run directly: bash scripts/test-gh-issue-ready.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_gh_issue_ready.py"
