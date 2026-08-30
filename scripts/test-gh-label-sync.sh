#!/usr/bin/env bash
# test-gh-label-sync.sh — hermetic tests for gh-label-sync.py's label provisioning.
#
# Wraps scripts/test_gh_label_sync.py (stdlib unittest, no network: run_gh is
# stubbed) so the idempotency and never-delete guarantees are exercised by the
# same `run scripts/test-*.sh` gate as the other hermetic tests.
#
# Run directly: bash scripts/test-gh-label-sync.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_gh_label_sync.py"
