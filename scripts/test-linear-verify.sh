#!/usr/bin/env bash
# test-linear-verify.sh — hermetic tests for linear-verify.py's checks.
#
# Wraps scripts/test_linear_verify.py (stdlib unittest, no network: run_gh() is
# stubbed against a fixture board) so the "a verifier that agrees with a broken
# board is worse than no verifier" guarantee is exercised by the same
# `run scripts/test-*.sh` gate as the other hermetic tests.
#
# Run directly: bash scripts/test-linear-verify.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_linear_verify.py"
