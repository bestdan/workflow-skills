#!/usr/bin/env bash
# test-co-review-size.sh — hermetic tests for scripts/co-review-size.py.
#
# Wraps scripts/test_co_review_size.py (stdlib unittest, no network) so the
# size gate is exercised by the same `run scripts/test-*.sh` gate as the other
# hermetic tests.
#
# Run directly: bash scripts/test-co-review-size.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_co_review_size.py"
