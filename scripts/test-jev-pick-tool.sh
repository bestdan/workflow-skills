#!/usr/bin/env bash
# test-jev-pick-tool.sh — hermetic tests for the tool-routing probe.
#
# Wraps scripts/test_jev_pick_tool.py (stdlib unittest). The routing ladder, the
# response reduction and the scorer all take their inputs directly, so there is no
# fixture and no network. No live counterpart, for the same reason as the collision
# instrument: nothing in this repo depends on the Jev API.
#
# Run directly: bash scripts/test-jev-pick-tool.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_jev_pick_tool.py"
