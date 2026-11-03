#!/usr/bin/env bash
# test-land-pr.sh — hermetic tests for land-pr.py, the shadow merge lander.
#
# Wraps scripts/test_land_pr.py (stdlib unittest, no network: run_gh is
# replaced by an in-memory repository) so the would-land case and every hold
# path run under the same `scripts/test-*.sh` gate as the other hermetic tests.
#
# Run directly: bash scripts/test-land-pr.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_land_pr.py"
