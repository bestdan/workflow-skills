#!/usr/bin/env bash
# test-linear-pr-resolve.sh — hermetic tests for linear-pr-resolve.py's
# three-source PR discovery, whole-token title post-filter, probe-failure
# handling and merge-state precedence.
#
# Wraps scripts/test_linear_pr_resolve.py (stdlib unittest, no network, no
# `gh` binary — the `run_gh` seam is stubbed) so the same `scripts/test-*.sh`
# gate exercises the PRE-73 regression, each source in isolation, the
# probe-failure → unresolved rule and the precedence table.
#
# Run directly: bash scripts/test-linear-pr-resolve.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_linear_pr_resolve.py"
