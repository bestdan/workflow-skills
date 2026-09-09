#!/usr/bin/env bash
# test-jira-resolve-transition.sh — hermetic tests for
# jira-resolve-transition.py's category/exact transition resolution.
#
# Wraps scripts/test_jira_resolve_transition.py (stdlib unittest, no network,
# no subprocess) so the same `scripts/test-*.sh` gate exercises the
# real-workflow shape, the Canceled-only terminal board, the exact-match
# fallback, and the ambiguous cases.
#
# Run directly: bash scripts/test-jira-resolve-transition.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_jira_resolve_transition.py"
