#!/usr/bin/env bash
# test-session-state-hook.sh — hermetic tests for session-state-hook.py.
#
# Wraps scripts/test_session_state_hook.py (stdlib unittest; HOME is a temp
# dir, so the opt-in config and the state directory are fixtures) so the
# opt-in gate, the line format and the never-fail rule run in the same
# `run scripts/test-*.sh` gate as the other hermetic tests.
#
# Run directly: bash scripts/test-session-state-hook.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_session_state_hook.py"
