#!/usr/bin/env bash
# test-tier-forecast.sh — hermetic tests for tier-forecast.py, the promoter's
# tier forecast and Intent-block check.
#
# Wraps scripts/test_tier_forecast.py (stdlib unittest; each case builds a
# throwaway git repo with GIT_CONFIG_GLOBAL=/dev/null) so it runs under the same
# `scripts/test-*.sh` gate as the other hermetic tests.
#
# Run directly: bash scripts/test-tier-forecast.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_tier_forecast.py"
