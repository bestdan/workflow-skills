#!/usr/bin/env bash
# test-co-review-status.sh — hermetic tests for co-review-status.py.
#
# Wraps scripts/test_co_review_status.py (stdlib unittest, no network: run_gh
# is replaced by a recorder, and one case puts a stub `gh` first on PATH) so
# the API path, context, states and the failure path run under the same
# `scripts/test-*.sh` gate as the other hermetic tests.
#
# Run directly: bash scripts/test-co-review-status.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_co_review_status.py"
