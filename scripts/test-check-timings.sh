#!/usr/bin/env bash
# test-check-timings.sh — hermetic tests for check-timings.py.
#
# Wraps scripts/test_check_timings.py (stdlib unittest, no network: each case
# writes its times and history files to a temp directory) so it runs under the
# same `scripts/test-*.sh` gate as the other hermetic tests.
#
# Run directly: bash scripts/test-check-timings.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_check_timings.py"
