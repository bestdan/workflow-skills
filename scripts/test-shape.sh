#!/usr/bin/env bash
# test-shape.sh — hermetic tests for the _shape.expect() payload gate.
#
# Wraps scripts/test_shape.py (stdlib unittest, no network) so the shape gate
# six assets now depend on is exercised by the same `run scripts/test-*.sh`
# gate as the other hermetic tests.
#
# Run directly: bash scripts/test-shape.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_shape.py"
