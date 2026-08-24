#!/usr/bin/env bash
# test-gh-label-write.sh — hermetic tests for gh-label-write.py's atomic label write.
#
# Wraps scripts/test_gh_label_write.py (stdlib unittest, no network: run_gh is
# stubbed and every call recorded) so the validate-then-PATCH guarantee is
# exercised by the same `run scripts/test-*.sh` gate as the other hermetic tests.
#
# Run directly: bash scripts/test-gh-label-write.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_gh_label_write.py"
