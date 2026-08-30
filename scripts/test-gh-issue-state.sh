#!/usr/bin/env bash
# test-gh-issue-state.sh — hermetic tests for gh-issue-state.py's atomic write.
#
# Wraps scripts/test_gh_issue_state.py (stdlib unittest, no network: run_gh is
# stubbed and every call recorded) so the validate-then-PATCH guarantee is
# exercised by the same `run scripts/test-*.sh` gate as the other hermetic tests.
#
# Run directly: bash scripts/test-gh-issue-state.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_gh_issue_state.py"
