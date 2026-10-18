#!/usr/bin/env bash
# test-gh-issue-claim.sh — hermetic tests for gh-issue-claim.py's claim lifecycle.
#
# Wraps scripts/test_gh_issue_claim.py (stdlib unittest, no network: run_gh is
# stubbed) so the branch-name model, the acquire election's exit-code
# contract, and the wip count are exercised by the same `run scripts/test-*.sh`
# gate as the other hermetic tests.
#
# Run directly: bash scripts/test-gh-issue-claim.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_gh_issue_claim.py"
