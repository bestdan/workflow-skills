#!/usr/bin/env bash
# test-linear-graph-analyze.sh — hermetic tests for linear-graph-analyze.py's
# cycle/order/inversion analysis.
#
# Wraps scripts/test_linear_graph_analyze.py (stdlib unittest, no network, no
# subprocess) so the same `scripts/test-*.sh` gate exercises the two-node
# cycle, the None-last priority ordering, the zero-priority-blocker
# inversion, and the estimate/createdAt tie-break.
#
# Run directly: bash scripts/test-linear-graph-analyze.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_linear_graph_analyze.py"
