#!/usr/bin/env bash
# test-check-cache.sh — hermetic tests for check-cache.py.
#
# Wraps scripts/test_check_cache.py (stdlib unittest, no network: each case
# builds its own git repo under a temp directory) so it runs under the same
# `scripts/test-*.sh` gate as the other hermetic tests.
#
# Run directly: bash scripts/test-check-cache.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_check_cache.py"
