#!/usr/bin/env bash
# test-linear-scan.sh — hermetic tests for linear-scan.py (gql stubbed, no network).
#
# Run directly: bash scripts/test-linear-scan.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_linear_scan.py"
