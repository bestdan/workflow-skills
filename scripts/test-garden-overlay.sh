#!/usr/bin/env bash
# test-garden-overlay.sh — hermetic tests for garden-overlay.py, the resolver
# for the `garden.importance_overlay` key that /garden-backlog and /doctor read.
#
# Wraps scripts/test_garden_overlay.py (stdlib unittest; no network).
#
# Run directly: bash scripts/test-garden-overlay.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_garden_overlay.py"
