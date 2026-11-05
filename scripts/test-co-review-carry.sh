#!/usr/bin/env bash
# test-co-review-carry.sh — hermetic tests for co-review-carry.py.
#
# Wraps scripts/test_co_review_carry.py (stdlib unittest, no network: run_gh is
# replaced by a fake comment thread that also answers statuses and compare) so
# the carry conditions run under the same `scripts/test-*.sh` gate as the
# other hermetic tests.
#
# Run directly: bash scripts/test-co-review-carry.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_co_review_carry.py"
