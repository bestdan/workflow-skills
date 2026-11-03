#!/usr/bin/env bash
# test-co-review-evidence.sh — hermetic tests for the co-review evidence record.
#
# Wraps scripts/test_co_review_evidence.py (stdlib unittest, no network: run_gh
# is replaced by the in-memory comment thread from test_co_review_stamp.py) so
# the write-then-read round trip through co-review-stamp.py --evidence and
# co-review-evidence.py runs under the same `scripts/test-*.sh` gate as the
# other hermetic tests.
#
# Run directly: bash scripts/test-co-review-evidence.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_co_review_evidence.py"
