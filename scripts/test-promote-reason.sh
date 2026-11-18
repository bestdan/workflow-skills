#!/usr/bin/env bash
# test-promote-reason.sh — hermetic tests for promote-reason.py, which picks
# the LOW reason a tracker promoter records.
#
# Wraps scripts/test_promote_reason.py (stdlib unittest, no network).
#
# Run directly: bash scripts/test-promote-reason.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_promote_reason.py"
