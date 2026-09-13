#!/usr/bin/env bash
# test-linear-export.sh — hermetic tests for linear-export.py's pagination and
# truncation guards.
#
# Wraps scripts/test_linear_export.py (stdlib unittest, no network: gql() is
# stubbed and every call recorded) so the "never write a silently truncated
# export" guarantee is exercised by the same `run scripts/test-*.sh` gate as
# the other hermetic tests.
#
# Run directly: bash scripts/test-linear-export.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_linear_export.py"
