#!/usr/bin/env bash
# test-linear-promote-blockers.sh — hermetic tests for linear-promote-blockers.py,
# the native-blocker hold /promote-tasks (linear) applies to Backlog candidates.
#
# Wraps scripts/test_linear_promote_blockers.py (stdlib unittest, no network:
# the fast path's gql seam is stubbed). Run directly:
#   bash scripts/test-linear-promote-blockers.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_linear_promote_blockers.py"
