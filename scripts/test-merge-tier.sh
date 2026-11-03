#!/usr/bin/env bash
# test-merge-tier.sh — hermetic tests for merge-tier.py.
#
# Wraps scripts/test_merge_tier.py (stdlib unittest, no network: each case
# writes a fixture policy and PR-facts file to a temp directory) so every tier
# rule and every fail-closed path runs under the same `scripts/test-*.sh` gate
# as the other hermetic tests.
#
# Run directly: bash scripts/test-merge-tier.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_merge_tier.py"
