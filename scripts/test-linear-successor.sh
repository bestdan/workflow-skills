#!/usr/bin/env bash
# test-linear-successor.sh — hermetic tests for linear-successor.py's writes.
#
# Wraps scripts/test_linear_successor.py (stdlib unittest, no network: gql() is
# stubbed against a fixture workspace) so the "idempotent per write, not per
# issue" guarantee is exercised by the same `run scripts/test-*.sh` gate as the
# other hermetic tests.
#
# Run directly: bash scripts/test-linear-successor.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_linear_successor.py"
