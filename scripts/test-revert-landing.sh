#!/usr/bin/env bash
# test-revert-landing.sh — hermetic tests for revert-landing.py, the script
# behind /revert-landing.
#
# Wraps scripts/test_revert_landing.py (stdlib unittest, no network: the
# lander's run_gh is replaced by recorded gh JSON) so it runs under the same
# `scripts/test-*.sh` gate as the other hermetic tests.
#
# Run directly: bash scripts/test-revert-landing.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_revert_landing.py"
