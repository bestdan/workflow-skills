#!/usr/bin/env bash
# test-linear-rank.sh — hermetic tests for linear-rank.py's ready-candidate
# gate and rank over MCP list_issues-shaped input.
#
# Wraps scripts/test_linear_rank.py (stdlib unittest, no network: the script
# under test never touches the network itself). Run directly:
#   bash scripts/test-linear-rank.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_linear_rank.py"
