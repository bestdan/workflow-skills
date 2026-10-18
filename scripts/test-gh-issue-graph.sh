#!/usr/bin/env bash
# test-gh-issue-graph.sh — hermetic tests for gh-issue-graph.py's native-graph read.
#
# Wraps scripts/test_gh_issue_graph.py (stdlib unittest, no network: run_gh is
# stubbed) so cycle detection over the real `blocked_by` edges, the stale-versus-
# satisfied split, priority inversion and the footer/edge reconciliation are
# exercised by the same `run scripts/test-*.sh` gate as the other hermetic tests.
#
# Run directly: bash scripts/test-gh-issue-graph.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_gh_issue_graph.py"
