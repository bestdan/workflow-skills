#!/usr/bin/env bash
# test-check-select.sh — hermetic tests for check-select.py.
#
# Wraps scripts/test_check_select.py (stdlib unittest, no network: the git
# cases build their own repo under a temp directory) so it runs under the same
# `scripts/test-*.sh` gate as the other hermetic tests.
#
# Run directly: bash scripts/test-check-select.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_check_select.py"
