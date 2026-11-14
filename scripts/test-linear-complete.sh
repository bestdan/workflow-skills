#!/usr/bin/env bash
# test-linear-complete.sh — hermetic tests for linear-complete.py (gql stubbed, no network).
#
# Run directly: bash scripts/test-linear-complete.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_linear_complete.py"
