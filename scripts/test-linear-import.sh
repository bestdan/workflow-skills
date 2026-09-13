#!/usr/bin/env bash
# test-linear-import.sh — hermetic tests for linear-import.py's --plan mode.
#
# Wraps scripts/test_linear_import.py (stdlib unittest, no network: the GitHub
# read is stubbed) so the crosswalk and every refusal path are exercised by the
# same `run scripts/test-*.sh` gate as the other hermetic tests.
#
# Run directly: bash scripts/test-linear-import.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_linear_import.py"
