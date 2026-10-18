#!/usr/bin/env bash
# test-diff-anchor-check.sh — hermetic tests for scripts/diff-anchor-check.py.
#
# Wraps scripts/test_diff_anchor_check.py (stdlib unittest, no network, no
# subprocess) so the anchor-check split is exercised by the same
# `run scripts/test-*.sh` gate as the other hermetic tests.
#
# Run directly: bash scripts/test-diff-anchor-check.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_diff_anchor_check.py"
