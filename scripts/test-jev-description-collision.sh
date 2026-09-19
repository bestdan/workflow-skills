#!/usr/bin/env bash
# test-jev-description-collision.sh — hermetic tests for the collision instrument.
#
# Wraps scripts/test_jev_description_collision.py (stdlib unittest; the pure
# functions take their inputs directly, so there is no fixture and no network).
# The live half is scripts/test-jev-description-collision-live.sh, which costs
# money and is excluded from the gate by check.sh's test-*-live.sh rule.
#
# Run directly: bash scripts/test-jev-description-collision.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_jev_description_collision.py"
