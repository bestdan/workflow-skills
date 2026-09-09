#!/usr/bin/env bash
# test-copilot-instructions.sh — hermetic tests for build-copilot-instructions.py.
#
# Wraps scripts/test_copilot_instructions.py (stdlib unittest, no network) so the
# marker parsing, link flattening and drift check are exercised by the same
# `run scripts/test-*.sh` gate as the other hermetic tests.
#
# Run directly: bash scripts/test-copilot-instructions.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_copilot_instructions.py"
