#!/usr/bin/env bash
# test-gh-issue-deps.sh — hermetic tests for gh-issue-deps.py's native edge writes.
#
# Wraps scripts/test_gh_issue_deps.py (stdlib unittest, no network: run_gh is
# stubbed) so the POST target, the database-id payload and create-missing-only
# idempotency are exercised by the same `run scripts/test-*.sh` gate as the
# other hermetic tests.
#
# Run directly: bash scripts/test-gh-issue-deps.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_gh_issue_deps.py"
