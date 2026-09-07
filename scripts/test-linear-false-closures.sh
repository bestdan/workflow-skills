#!/usr/bin/env bash
# test-linear-false-closures.sh — hermetic tests for linear-false-closures.py's
# --prs-file path (the alternative to --repo for hosts where `gh` cannot reach
# the GitHub API).
#
# Wraps scripts/test_linear_false_closures.py (stdlib unittest, no network: gql,
# get_key, and subprocess.run are stubbed) so the same `run scripts/test-*.sh`
# gate picks it up.
#
# Run directly: bash scripts/test-linear-false-closures.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_linear_false_closures.py"
