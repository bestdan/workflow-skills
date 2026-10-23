#!/usr/bin/env bash
# test-co-review-stamp.sh — hermetic tests for co-review-stamp.py.
#
# Wraps scripts/test_co_review_stamp.py (stdlib unittest, no network: run_gh is
# replaced by an in-memory comment thread) so create, update-in-place and the
# non-fatal failure path run under the same `scripts/test-*.sh` gate as the
# other hermetic tests.
#
# Run directly: bash scripts/test-co-review-stamp.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_co_review_stamp.py"
