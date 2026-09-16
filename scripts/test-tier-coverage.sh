#!/usr/bin/env bash
# test-tier-coverage.sh — hermetic tests for the typecheck tier partition.
#
# Wraps scripts/test_tier_coverage.py (stdlib unittest; classify() and
# patterns() take their inputs directly, so no repository fixture and no
# subprocess). Each case reproduces one of the two tiering mistakes that
# motivated the checker.
#
# Run directly: bash scripts/test-tier-coverage.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_tier_coverage.py"
