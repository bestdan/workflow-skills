#!/usr/bin/env bash
# test-jev-description-collision.sh — hermetic tests for the collision instrument.
#
# Wraps scripts/test_jev_description_collision.py (stdlib unittest; the pure
# functions take their inputs directly, so there is no fixture and no network).
# There is no live counterpart on purpose — nothing here depends on the Jev API,
# so a standing test against it would outlive its reason to exist.
#
# Run directly: bash scripts/test-jev-description-collision.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_jev_description_collision.py"
