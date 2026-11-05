#!/usr/bin/env bash
# test-review-queue.sh — hermetic tests for review-queue.py.
#
# Wraps scripts/test_review_queue.py (stdlib unittest, no network: the
# lander's run_gh is replaced by an in-memory repository) so it runs under the
# same `scripts/test-*.sh` gate as the other hermetic tests.
#
# Run directly: bash scripts/test-review-queue.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_review_queue.py"
