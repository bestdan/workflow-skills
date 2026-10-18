#!/usr/bin/env bash
# test-linear-ready.sh — hermetic tests for linear-ready.py's candidate selection.
#
# Wraps scripts/test_linear_ready.py (stdlib unittest, no network:
# fetch_issues/resolve_team/get_key are stubbed) so the Unassigned-bucket
# exclusion pass (PRE-501) is exercised by the same `run scripts/test-*.sh`
# gate as the other hermetic tests. This is distinct from the opt-in
# scripts/test-linear-ready-live.sh, which hits the real Linear API and is
# not run by check.sh.
#
# Run directly: bash scripts/test-linear-ready.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_linear_ready.py"
