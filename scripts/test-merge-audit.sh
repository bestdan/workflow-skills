#!/usr/bin/env bash
# test-merge-audit.sh — hermetic tests for merge-audit.py, the review and
# shadow audits.
#
# Wraps scripts/test_merge_audit.py (stdlib unittest, no network: the lander's
# run_gh is replaced by recorded gh JSON) so both modes run under the same
# `scripts/test-*.sh` gate as the other hermetic tests.
#
# Run directly: bash scripts/test-merge-audit.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_merge_audit.py"
