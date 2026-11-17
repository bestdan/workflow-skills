#!/usr/bin/env bash
# test-garden-checks.sh — hermetic tests for garden-checks.py, the
# deterministic checks around the /garden-backlog judges
# (commands/garden-backlog.md §4).
#
# Wraps scripts/test_garden_checks.py (stdlib unittest; `gh` is stubbed and
# the git search runs on a throwaway fixture repo, so no network).
#
# Run directly: bash scripts/test-garden-checks.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_garden_checks.py"
