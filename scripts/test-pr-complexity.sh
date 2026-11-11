#!/usr/bin/env bash
# test-pr-complexity.sh — hermetic tests for pr-complexity.py.
#
# Wraps scripts/test_pr_complexity.py (stdlib unittest, no network) so the diff
# parser, the score formula, the exclusions and the config block run under the
# same `scripts/test-*.sh` gate as the other hermetic tests.
#
# Run directly: bash scripts/test-pr-complexity.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_pr_complexity.py"
